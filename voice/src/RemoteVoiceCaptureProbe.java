// SPDX-License-Identifier: Apache-2.0
package com.mi.bluetooth.services.voice.mitvvoice;

import android.bluetooth.*;
import android.content.Context;
import android.os.*;
import java.util.*;

/** Bounded physical push-to-talk diagnostic. Default mode counts/discards PCM;
 * explicit Google mode supplies it to the official assistant. No audio files,
 * bonding,
 * keymap changes or firmware service access. Four voice CCCDs are temporarily
 * enabled, with their exact previous values restored before disconnect. */
public final class RemoteVoiceCaptureProbe extends MitvVoiceCallback {
 static final UUID HID=UUID.fromString("00001812-0000-1000-8000-00805f9b34fb");
 static final UUID REPORT=UUID.fromString("00002a4d-0000-1000-8000-00805f9b34fb");
 static Handler handler;
 static RemoteVoiceCaptureProbe self;
 static boolean googleMode,repeatMode,dailyMode;
 static long lifetimeMs=180000;
 Context context;BluetoothDevice remote;GoogleVoiceRoute google;
 BluetoothGatt gatt; BluetoothGattCharacteristic enable;
 MitvVoice decoder;
 final ArrayList<BluetoothGattCharacteristic> reports=new ArrayList<>();
 final ArrayDeque<byte[]> writes=new ArrayDeque<>();
 boolean writing,pressed,closing,finished,everEnabled,commandEnabled,ready,restoring,servicesReady,setupStarted;
 final LinkedHashMap<BluetoothGattDescriptor,byte[]> originalDescriptors=new LinkedHashMap<>();
 final ArrayDeque<BluetoothGattDescriptor> restoreQueue=new ArrayDeque<>();
 long samples,nonzero,frames,energy;int peak;
 long utteranceSamples,utteranceEnergy;
 final int[] reportCounts=new int[8];
 int descriptorIndex=4;
 int utteranceId;
 long utteranceBegin,firstPacket,lastPacket,packetCount,packetGapMax;
 int lastSequence=-1,sequenceGaps,sequenceRepeats;
 final long[] binSamples=new long[40],binEnergy=new long[40];
 final java.util.concurrent.ExecutorService routeWorker=java.util.concurrent.Executors.newSingleThreadExecutor();
 void readNextDescriptor(){
  if(descriptorIndex>7){
   Runnable markReady=()->{if(closing)return;ready=true;log("READY physicalVoiceKeyOnly=true timeoutSeconds="+(lifetimeMs/1000)+" pcmSaved=false googleMode="+googleMode+" repeatedPresses="+repeatMode);};
   if(googleMode){
    routeWorker.execute(()->{
     try{google=new GoogleVoiceRoute(context,remote);google.setup();handler.post(markReady);}
     catch(Exception e){handler.post(()->{log("GOOGLE_SETUP_ERROR "+e);finish("google-setup-failed");});}
    });
   }else markReady.run();
   return;
  }
  BluetoothGattDescriptor d=reports.get(descriptorIndex).getDescriptor(UUID.fromString("00002902-0000-1000-8000-00805f9b34fb"));
  if(d==null||!gatt.readDescriptor(d))finish("descriptor-read-rejected");
 }
 static void log(String s){System.out.println(s);System.out.flush();}
 void writeNext(){
  if(writing||writes.isEmpty()||enable==null||finished)return;
  byte[] data=writes.removeFirst();enable.setValue(data);
  enable.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
  writing=gatt.writeCharacteristic(enable);
  log("VOICE_COMMAND enable="+(data[4]==1)+" accepted="+writing);
  if(!writing)finish("write-rejected");
 }
 @Override void sendCommand(byte[] data){
  if(data.length!=20||data[3]!=(byte)128||(data[4]!=0&&data[4]!=1))throw new SecurityException("Unexpected OEM command");
  for(int i=0;i<data.length;i++)if(i!=3&&i!=4&&data[i]!=0)throw new SecurityException("Unexpected command payload");
  if(data[4]==1){if(!pressed||closing)return;everEnabled=true;}
  commandEnabled=data[4]==1;
  writes.add(data.clone());writeNext();
 }
 void disable(){byte[] b=new byte[20];b[3]=(byte)128;sendCommand(b);}
 @Override void notifyAudioStart(){
  log("OEM_AUDIO_START physicalKeyHeld="+pressed);final int id=utteranceId;
  Runnable launch=()->{
   if(closing||utteranceId!=id)return;
   // Resetting the HAL can block. Never stall the same Looper that delivers
   // Bluetooth reports and runs the original decoder. A released long press
   // still owns buffered speech and must be allowed to launch recognition.
   if(google!=null)routeWorker.execute(()->{
    try{
     google.launch();
     if(repeatMode){
      java.nio.file.Path ack=java.nio.file.Paths.get(System.getProperty("java.library.path"),"assistant-launched");
      boolean prepared=false;
      for(int n=0;n<100;n++){
       try{if(Integer.toString(id).equals(new String(java.nio.file.Files.readAllBytes(ack)).trim())){prepared=true;break;}}catch(java.io.IOException ignored){}
       Thread.sleep(50);
      }
      if(!prepared)throw new java.io.IOException("Assistant launch not acknowledged");
      handler.post(()->{if(!closing&&utteranceId==id)google.assistantLaunched();});
     }
    }catch(Exception e){handler.post(()->{log("GOOGLE_LAUNCH_ERROR "+e);finish("google-launch-failed");});}
   });
  };
  // Retain the original input, and wait for public ASSIST launch completion.
  // Google may reuse an existing AudioRecord instead of opening a new socket.
  launch.run();
 }
 @Override void notifyAudioStop(){log("OEM_AUDIO_STOP");disable();}
 @Override void notifyStartSearch(){log("SHORT_PRESS noAssistantStarted=true");}
 @Override void notifyEncodedFrameSize(int size){frames++;}
 @Override void outputAudioData(short[] pcm){
  int bin=(int)Math.min(39,Math.max(0,(SystemClock.elapsedRealtime()-utteranceBegin)/250));
  binSamples[bin]+=pcm.length;for(short x:pcm)binEnergy[bin]+=(long)x*x;
  for(short x:pcm){samples++;if(x!=0)nonzero++;int v=Math.abs((int)x);if(v>peak)peak=v;energy+=(long)x*x;}
  if(google!=null)google.feed(pcm);
 }
 void finish(String reason){
  if(closing)return;closing=true;log("ENDING reason="+reason);
  if(decoder!=null)decoder.onSystemKey(new byte[8]);
  if(commandEnabled)disable();
  handler.postDelayed(()->{
   if(decoder!=null)decoder.onExit();
   prepareRestore();
  },800);
  handler.postDelayed(()->close("restore-timeout"),5000);
 }
 void prepareRestore(){
  if(finished)return;
  if(writing||!writes.isEmpty()){handler.postDelayed(()->prepareRestore(),100);return;}
  restoring=true;restoreQueue.addAll(originalDescriptors.keySet());restoreNext();
 }
 void restoreNext(){
  if(restoreQueue.isEmpty()){close("restored");return;}
  BluetoothGattDescriptor d=restoreQueue.removeFirst();d.setValue(originalDescriptors.get(d));
  if(!gatt.writeDescriptor(d))close("restore-rejected");
 }
 void close(String state){
   if(finished)return;
   if(gatt!=null){for(int i:new int[]{0,4,5,6,7})if(i<reports.size())gatt.setCharacteristicNotification(reports.get(i),false);gatt.close();}
   if(google!=null)google.close();
   routeWorker.shutdownNow();
   finished=true;
   log("CCCD_CLEANUP "+state);
   log("REPORT_COUNTS "+Arrays.toString(reportCounts));
   log("RESULT frames="+frames+" samples="+samples+" nonzero="+nonzero+" peak="+peak+" rms="+(samples==0?0:Math.sqrt((double)energy/samples))+" pcmSaved=false googleSpeechMode="+googleMode);
   System.exit(samples>0&&"restored".equals(state)?0:2);
 }
 void setup(BluetoothGatt g){
  if(closing||setupStarted||!servicesReady)return;
  setupStarted=true;
  BluetoothGattService service=g.getService(HID);
  if(service==null){finish("missing-hid");return;}
  for(BluetoothGattCharacteristic c:service.getCharacteristics())if(REPORT.equals(c.getUuid()))reports.add(c);
  if(reports.size()<8){finish("missing-reports");return;}
  // Exact inventory recorded in the same boot; refuse a different layout.
  int[] ids={46,50,53,57,60,64,68,72};
  for(int i=0;i<8;i++)if(reports.get(i).getInstanceId()!=ids[i]){finish("unexpected-report-layout");return;}
  enable=reports.get(3);
  decoder=new MitvVoice(this,Looper.getMainLooper());
  for(int i:new int[]{0,4,5,6,7})if(!g.setCharacteristicNotification(reports.get(i),true)){finish("notification-rejected");return;}
  readNextDescriptor();
 }
 void changed(BluetoothGattCharacteristic c,byte[] bytes){
  if(closing||!ready||decoder==null||bytes==null)return;
  int i=reports.indexOf(c);
  if(i<0)for(int n=0;n<reports.size();n++)if(reports.get(n).getInstanceId()==c.getInstanceId()){i=n;break;}
  if(i>=0&&i<8){reportCounts[i]++;if(i==4)log("VOICE_STATUS len="+bytes.length+" first="+(bytes.length>0?(bytes[0]&255):-1)+" second="+(bytes.length>1?(bytes[1]&255):-1));else if(i>=5&&reportCounts[i]<=2)log("AUDIO_REPORT index="+i+" len="+bytes.length);}
  if(i==0&&bytes.length>=3){
   if(bytes[2]==62&&!pressed){pressed=true;utteranceSamples=samples;utteranceEnergy=energy;utteranceBegin=SystemClock.elapsedRealtime();firstPacket=lastPacket=packetCount=packetGapMax=0;lastSequence=-1;sequenceGaps=sequenceRepeats=0;Arrays.fill(binSamples,0);Arrays.fill(binEnergy,0);final int id=++utteranceId;if(google!=null)google.beginUtterance();log("PHYSICAL_VOICE_DOWN utterance="+id+" elapsedMs="+utteranceBegin);decoder.onSystemKey(bytes);handler.postDelayed(()->{if(pressed&&utteranceId==id)finish("hold-limit-10s");},10000);}
   else if(bytes[2]==0&&bytes[0]==0&&pressed){pressed=false;log("PHYSICAL_VOICE_UP utterance="+utteranceId+" heldMs="+(SystemClock.elapsedRealtime()-utteranceBegin));long count=samples-utteranceSamples;log("UTTERANCE_PCM samples="+count+" rms="+(count==0?0:Math.sqrt((double)(energy-utteranceEnergy)/count)));log("PACKET_TIMING count="+packetCount+" spanMs="+(lastPacket-firstPacket)+" maxGapMs="+packetGapMax+" sequenceGaps="+sequenceGaps+" sequenceRepeats="+sequenceRepeats);for(int n=0;n<40;n++)if(binSamples[n]>0)log("PCM_BIN offsetMs="+(n*250)+" samples="+binSamples[n]+" rms="+Math.sqrt((double)binEnergy[n]/binSamples[n]));decoder.onSystemKey(bytes);if(repeatMode)log("READY_FOR_NEXT_PRESS microphoneStopRequested=true");else handler.postDelayed(()->finish("one-press-complete"),googleMode?5000:500);}
  }else if(i==4&&bytes.length>=2){decoder.onStreamEnableRead(bytes);}
  else if(i>=5&&i<=7&&pressed&&bytes.length==60&&bytes[0]==1&&bytes[2]==(byte)0xad){
   long now=SystemClock.elapsedRealtime();if(packetCount++==0)firstPacket=now;else packetGapMax=Math.max(packetGapMax,now-lastPacket);lastPacket=now;
   int header=bytes[1]&255,seq=header==8?0:header==56?1:header==200?2:header==248?3:-1;
   if(seq>=0&&lastSequence>=0){if(seq==lastSequence)sequenceRepeats++;else if(seq!=((lastSequence+1)&3))sequenceGaps++;}lastSequence=seq;
   // Two transport bytes plus the 57-byte mSBC frame and padding. The OEM
   // JNI consumes 60 encoded bytes after stripping the transport header.
   // Supply two trailing zero bytes so the native read stays within bounds;
   // the independent synthetic fixture already verified this padding.
   decoder.onAudioData(Arrays.copyOf(bytes,62));
  }
 }
 public static void main(String[] args)throws Exception{
  dailyMode=args.length==1&&"google-daily".equals(args[0]);
  repeatMode=dailyMode||(args.length==1&&"google-loop".equals(args[0]));
  googleMode=repeatMode||(args.length==1&&"google".equals(args[0]));
  if(args.length!=0&&!googleMode)throw new IllegalArgumentException();
  Class<?> props=Class.forName("android.os.SystemProperties");
  if(!"14".equals(props.getMethod("get",String.class).invoke(null,"ro.build.version.release"))||!"1".equals(props.getMethod("get",String.class).invoke(null,"ro.codex.fileboot")))throw new SecurityException("Google RAM only");
  if(dailyMode&&!new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("/dev/codex-daily-v1")),java.nio.charset.StandardCharsets.US_ASCII).equals("shared-default-v1\n"))throw new SecurityException("Daily profile required");
  if(repeatMode&&!dailyMode){
   String up=new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("/proc/uptime")));
   lifetimeMs=Math.min(3300000,3300000-(long)(Double.parseDouble(up.split(" ")[0])*1000));
   if(lifetimeMs<60000)throw new IllegalStateException("Too near unchanged trial deadline");
  }
  android.system.Os.setgid(1000);android.system.Os.setuid(1000);
  Looper.prepareMainLooper();handler=new Handler(Looper.getMainLooper());self=new RemoteVoiceCaptureProbe();
  String libpath=System.getProperty("java.library.path","");
  if(repeatMode&&libpath.startsWith("/data/local/tmp/codex-voice-device-")){
   final java.io.File stop=new java.io.File(libpath,"stop-request");
   handler.postDelayed(new Runnable(){public void run(){if(self.finished)return;if(stop.exists())self.finish("requested-stop");else handler.postDelayed(this,500);}},500);
  }
  Class<?> init=Class.forName("android.bluetooth.BluetoothFrameworkInitializer"),manager=Class.forName("android.os.BluetoothServiceManager");
  if(init.getMethod("getBluetoothServiceManager").invoke(null)==null)init.getMethod("setBluetoothServiceManager",manager).invoke(null,manager.getConstructor().newInstance());
  Class<?> at=Class.forName("android.app.ActivityThread");Object thread=at.getMethod("systemMain").invoke(null);Context ctx=(Context)at.getMethod("getSystemContext").invoke(thread);
  BluetoothDevice remote=null;
  for(BluetoothDevice d:((BluetoothManager)ctx.getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter().getBondedDevices())if("小米蓝牙语音遥控器".equals(d.getName())){if(remote!=null)throw new IllegalStateException("Ambiguous");remote=d;}
  if(remote==null)throw new IllegalStateException("Existing bond required");
  self.context=ctx;self.remote=remote;
  if(!dailyMode)handler.postDelayed(()->self.finish("bounded-lifetime-end"),lifetimeMs);
  self.gatt=remote.connectGatt(ctx,false,new BluetoothGattCallback(){
   public void onConnectionStateChange(BluetoothGatt g,int status,int state){handler.post(()->{log("GATT status="+status+" state="+state);if(status!=0)self.finish("connection-error");else if(state==2)g.discoverServices();});}
   public void onServicesDiscovered(BluetoothGatt g,int status){handler.post(()->{if(self.closing)return;log("SERVICES status="+status);if(status!=0)self.finish("discovery-error");else{self.servicesReady=true;if(!g.requestMtu(120))self.finish("mtu-rejected");}});}
   public void onMtuChanged(BluetoothGatt g,int mtu,int status){handler.post(()->{log("MTU status="+status+" size="+mtu);if(!self.servicesReady||self.closing)return;if(status==0&&mtu>=65)self.setup(g);else self.finish("mtu-too-small");});}
   public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c){byte[] b=c.getValue();final byte[] copy=b==null?null:b.clone();handler.post(()->self.changed(c,copy));}
   // Android 13+ supplies the value belonging to this notification. Reading
   // the shared characteristic later can observe a newer packet instead.
   public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] value){final byte[] copy=value==null?null:value.clone();handler.post(()->self.changed(c,copy));}
   public void onDescriptorRead(BluetoothGatt g,BluetoothGattDescriptor d,int status){final byte[] v=d.getValue()==null?null:d.getValue().clone();handler.post(()->{
    if(self.closing)return;
    log("CCCD index="+self.descriptorIndex+" status="+status+" value="+Arrays.toString(v));
    if(status!=0||v==null||v.length!=2||v[1]!=0||(v[0]!=0&&v[0]!=1)){self.finish("descriptor-read-failed");return;}
    if(v[0]==0){self.originalDescriptors.put(d,v);d.setValue(new byte[]{1,0});if(!g.writeDescriptor(d))self.finish("descriptor-enable-rejected");}
    else{self.descriptorIndex++;self.readNextDescriptor();}
   });}
   public void onDescriptorWrite(BluetoothGatt g,BluetoothGattDescriptor d,int status){handler.post(()->{
    log("CCCD_WRITE charInstance="+d.getCharacteristic().getInstanceId()+" status="+status+" restoring="+self.restoring);
    if(self.restoring){if(status!=0)self.close("restore-failed");else self.restoreNext();}
    else if(status!=0)self.finish("descriptor-enable-failed");
    else if(!self.closing){self.descriptorIndex++;self.readNextDescriptor();}
   });}
   public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic c,int status){handler.post(()->{self.writing=false;log("WRITE status="+status);if(status!=0)self.finish("write-failed");else self.writeNext();});}
  },BluetoothDevice.TRANSPORT_LE);
  if(self.gatt==null)self.finish("no-gatt");
  Looper.loop();
 }
}
