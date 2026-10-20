// SPDX-License-Identifier: Apache-2.0
package com.mi.bluetooth.services.voice.mitvvoice;

import android.bluetooth.BluetoothDevice;
import android.content.*;
import android.media.*;
import android.net.*;
import android.os.SystemClock;
import java.io.*;
import java.util.concurrent.*;

/** Original PCM socket and input route, with Google TV's resolved ASSIST UI.
 * Bounded owner controls lifetime. No PCM files; only physical-key audio. */
final class GoogleVoiceRoute {
 final Context context;final AudioManager audio;final BluetoothDevice remote;
 final java.lang.reflect.Method connection;
 final ArrayBlockingQueue<byte[]> pcm=new ArrayBlockingQueue<>(2048);
 final ArrayBlockingQueue<byte[]> preroll=new ArrayBlockingQueue<>(2048);
 final Object audioLock=new Object();
 boolean buffering,waitingForRecognition;
 volatile boolean stopped;volatile LocalSocket client;
 LocalServerSocket server;boolean announced;
 GoogleVoiceRoute(Context c,BluetoothDevice r)throws Exception{
  context=c;remote=r;audio=(AudioManager)c.getSystemService(Context.AUDIO_SERVICE);
  connection=AudioManager.class.getMethod("setWiredDeviceConnectionState",int.class,int.class,String.class,String.class);
 }
 void setup()throws Exception{
  for(AudioDeviceInfo d:audio.getDevices(AudioManager.GET_DEVICES_INPUTS))if(d.getType()==AudioDeviceInfo.TYPE_WIRED_HEADSET&&remote.getAddress().equals(d.getAddress()))throw new IllegalStateException("Remote input already announced");
  server=new LocalServerSocket("com.mi.bluetooth.MiVoiceControl.VoiceScoket");
  Thread accept=new Thread(()->{
   try{
    while(!stopped){
     final LocalSocket s=server.accept();
     if(s.getPeerCredentials().getUid()!=1041){s.close();continue;}
     if(client!=null)client.close();client=s;
     synchronized(audioLock){
      if(waitingForRecognition)releasePreroll();
     }
     RemoteVoiceCaptureProbe.log("GOOGLE_AUDIO_HAL_SOCKET_CONNECTED");
     Thread writer=new Thread(()->{
      try{
       OutputStream out=s.getOutputStream();
       byte[] pending=null;int offset=0;long deadline=SystemClock.elapsedRealtime()+25;
       while(!stopped&&client==s){
        // Match the original HAL's 400-sample reads. Never interleave whole
        // silence packets between variable-sized BLE bursts or burst backlog.
        byte[] data=new byte[800];int filled=0;
        while(filled<data.length){
         if(pending==null){long wait=deadline-SystemClock.elapsedRealtime();pending=wait>0?pcm.poll(wait,TimeUnit.MILLISECONDS):pcm.poll();offset=0;}
         if(pending==null)break;
         int count=Math.min(data.length-filled,pending.length-offset);
         System.arraycopy(pending,offset,data,filled,count);filled+=count;offset+=count;
         if(offset==pending.length)pending=null;
        }
        long wait=deadline-SystemClock.elapsedRealtime();if(wait>0)Thread.sleep(wait);
        out.write(data);deadline+=25;
        if(SystemClock.elapsedRealtime()-deadline>100)deadline=SystemClock.elapsedRealtime()+25;
       }
      }catch(Exception e){RemoteVoiceCaptureProbe.log("GOOGLE_AUDIO_SOCKET_END "+e.getClass().getSimpleName());}
      finally{synchronized(audioLock){if(client==s)client=null;}try{s.close();}catch(IOException ignored){}}
     },"GoogleRemotePcm");writer.setDaemon(true);writer.start();
    }
   }catch(Exception e){if(!stopped)RemoteVoiceCaptureProbe.log("GOOGLE_SOCKET_ERROR "+e.getClass().getSimpleName());}
  },"GoogleRemoteAccept");accept.setDaemon(true);accept.start();
  connection.invoke(audio,0x80000010,1,remote.getAddress(),remote.getName());announced=true;
  for(int n=0;n<30;n++){
   for(AudioDeviceInfo d:audio.getDevices(AudioManager.GET_DEVICES_INPUTS))if(d.getType()==AudioDeviceInfo.TYPE_WIRED_HEADSET&&remote.getAddress().equals(d.getAddress())){RemoteVoiceCaptureProbe.log("GOOGLE_REMOTE_INPUT_READY id="+d.getId());return;}
   Thread.sleep(100);
  }
  throw new IllegalStateException("Remote route unavailable");
 }
 synchronized void resetInput()throws Exception{
  if(stopped)throw new IOException("Remote route already closed");
  if(announced){connection.invoke(audio,0x80000010,0,remote.getAddress(),remote.getName());announced=false;}
  boolean absent=false;
  for(int n=0;n<30;n++){
   boolean present=false;for(AudioDeviceInfo d:audio.getDevices(AudioManager.GET_DEVICES_INPUTS))if(d.getType()==AudioDeviceInfo.TYPE_WIRED_HEADSET&&remote.getAddress().equals(d.getAddress()))present=true;
   if(!present){absent=true;break;}Thread.sleep(100);
  }
  if(!absent)throw new IOException("Previous remote input did not disconnect");
  if(client!=null){client.close();client=null;}pcm.clear();
  // Preserve the listening socket: closing it while accept() is blocked can
  // leave its abstract address bound. Only recycle the HAL input and client.
  Thread.sleep(600);
  connection.invoke(audio,0x80000010,1,remote.getAddress(),remote.getName());announced=true;
  for(int n=0;n<30;n++){
   for(AudioDeviceInfo d:audio.getDevices(AudioManager.GET_DEVICES_INPUTS))if(d.getType()==AudioDeviceInfo.TYPE_WIRED_HEADSET&&remote.getAddress().equals(d.getAddress())){RemoteVoiceCaptureProbe.log("GOOGLE_REMOTE_INPUT_RESET id="+d.getId());return;}
   Thread.sleep(100);
  }
  throw new IOException("Fresh remote input unavailable");
 }
 void launch(){
  synchronized(audioLock){waitingForRecognition=buffering;}
  // The supervisor invokes only the public ASSIST intent. The on-device
  // supervisor retains the authorized trial root identity; GATT and PCM run
  // as system UID. No activity permission or package checks are changed.
  RemoteVoiceCaptureProbe.log("GOOGLE_ASSIST_REQUEST");
 }
 void releasePreroll(){
  waitingForRecognition=false;buffering=false;int bytes=0;byte[] packet;
  while((packet=preroll.poll())!=null){pcm.offer(packet);bytes+=packet.length;}
  RemoteVoiceCaptureProbe.log("PRE_ROLL_RELEASED samples="+(bytes/2));
 }
 void assistantLaunched(){synchronized(audioLock){if(waitingForRecognition&&client!=null)releasePreroll();}}
 void beginUtterance(){synchronized(audioLock){preroll.clear();pcm.clear();buffering=true;waitingForRecognition=false;}}
 void feed(short[] values){
  byte[] b=new byte[values.length*2];for(int n=0;n<values.length;n++){b[n*2]=(byte)values[n];b[n*2+1]=(byte)(values[n]>>>8);}
  synchronized(audioLock){if(stopped)return;ArrayBlockingQueue<byte[]> q=buffering?preroll:pcm;if(!q.offer(b)){q.poll();q.offer(b);}}
 }
 synchronized void close(){
  stopped=true;pcm.clear();preroll.clear();
  try{if(client!=null)client.close();}catch(Exception ignored){}
  try{if(server!=null)server.close();}catch(Exception ignored){}
  if(announced)try{connection.invoke(audio,0x80000010,0,remote.getAddress(),remote.getName());RemoteVoiceCaptureProbe.log("GOOGLE_REMOTE_INPUT_DISCONNECTED");}catch(Exception e){RemoteVoiceCaptureProbe.log("GOOGLE_ROUTE_CLEANUP_FAILED "+e.getClass().getSimpleName());}
 }
}
