// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
import android.content.ComponentName;
import android.content.Context;
import android.hardware.hdmi.HdmiDeviceInfo;
import android.media.tv.*;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Surface;
import android.view.KeyEvent;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.Executor;

/** Original TV HAL passthrough; no donor HDMI codec, JNI, network or flash code. */
public class MainHdmiService extends TvInputService {
 static final String TAG="OriginalHdmi";
 final Map<Integer,TvInputInfo> inputs=new HashMap<>();
 final Map<Integer,Integer> ports=new HashMap<>();
 final Map<Integer,TvInputHardwareInfo> hardwareInfos=new HashMap<>();
 final Map<Integer,HdmiDeviceInfo> cecDevices=new TreeMap<>();
 final Map<Integer,TvInputInfo> cecInputs=new HashMap<>();
 final Set<HdmiSession> modeOwners=new HashSet<>();
 Object manager;
 HdmiSession cecOwner;
 static Object call(Object obj,String method,Class<?>[] types,Object... args)throws Exception {
  return obj.getClass().getMethod(method,types).invoke(obj,args);
 }
 @Override public void onCreate(){super.onCreate();manager=getSystemService("tv_input");}
 TvInputInfo inputInfo(TvInputHardwareInfo hw)throws Exception {
  int port=hw.getHdmiPortId();
  TvInputInfo.Builder b=new TvInputInfo.Builder(this,new ComponentName(this,getClass()));
  call(b,"setTvInputHardwareInfo",new Class<?>[]{TvInputHardwareInfo.class},hw);
  String name=null;
  for(HdmiDeviceInfo peer:cecDevices.values())if(peer.getPortId()==port){name=peer.getDisplayName();break;}
  call(b,"setLabel",new Class<?>[]{CharSequence.class},CecInputLabel.device(port,name));
  return b.build();
 }
 void refreshLabel(int port){
  Integer id=ports.get(port);if(id==null)return;
  try{TvInputInfo info=inputInfo(hardwareInfos.get(id));call(manager,"updateTvInputInfo",new Class<?>[]{TvInputInfo.class},info);inputs.put(id,info);Log.i(TAG,"Updated CEC label port="+port);}
  catch(Exception e){Log.w(TAG,"CEC label update failed",e);}
 }
 TvInputInfo cecInputInfo(HdmiDeviceInfo device)throws Exception {
  Integer id=ports.get(device.getPortId());if(id==null)return null;
  TvInputInfo parent=inputs.get(id);if(parent==null)return null;
  TvInputInfo.Builder b=new TvInputInfo.Builder(this,new ComponentName(this,getClass()));
  call(b,"setHdmiDeviceInfo",new Class<?>[]{HdmiDeviceInfo.class},device);
  call(b,"setParentId",new Class<?>[]{String.class},parent.getId());
  call(b,"setLabel",new Class<?>[]{CharSequence.class},CecInputLabel.device(device.getPortId(),device.getDisplayName()));
  return b.build();
 }
 // LiveTV forwards navigation only if a genuine CEC child exists for this input.
 public TvInputInfo onHdmiDeviceAdded(HdmiDeviceInfo device){
  if(device!=null&&device.isCecDevice()&&device.getPortId()>=1&&device.getPortId()<=3){
   cecDevices.put(device.getLogicalAddress(),device);refreshLabel(device.getPortId());
   if(cecOwner!=null&&cecOwner.device-4==device.getPortId()&&cecOwner.attached)cecOwner.startCec();
   try{TvInputInfo info=cecInputInfo(device);if(info!=null){cecInputs.put(device.getLogicalAddress(),info);Log.i(TAG,"Registered CEC child port="+device.getPortId());}return info;}
   catch(Exception e){Log.w(TAG,"CEC child registration failed",e);}
  }
  return null;
 }
 public void onHdmiDeviceUpdated(HdmiDeviceInfo device){
  if(device!=null&&cecDevices.containsKey(device.getLogicalAddress())){
   cecDevices.put(device.getLogicalAddress(),device);refreshLabel(device.getPortId());
   if(cecInputs.containsKey(device.getLogicalAddress()))try{TvInputInfo info=cecInputInfo(device);if(info!=null){call(manager,"updateTvInputInfo",new Class<?>[]{TvInputInfo.class},info);cecInputs.put(device.getLogicalAddress(),info);}}
   catch(Exception e){Log.w(TAG,"CEC child update failed",e);}
  }
 }
 public String onHdmiDeviceRemoved(HdmiDeviceInfo device){
  if(device!=null){cecDevices.remove(device.getLogicalAddress());refreshLabel(device.getPortId());TvInputInfo old=cecInputs.remove(device.getLogicalAddress());return old==null?null:old.getId();}
  return null;
 }
 // Hidden system API override. Exact live Android method signature.
 public TvInputInfo onHardwareAdded(TvInputHardwareInfo hw){
  int id=hw.getDeviceId();
  // Domestic L75MA-SPL OEM configuration maps only physical HDMI 1-3.
  if(hw.getType()!=9||id<5||id>7)return null;
  int port=hw.getHdmiPortId();
  if(port<1||port>3||ports.containsKey(port))return null;
  try {
   TvInputInfo info=inputInfo(hw);hardwareInfos.put(id,hw);
   inputs.put(id,info);ports.put(port,id);Log.i(TAG,"Registered main HDMI port="+port+" device="+id);return info;
  }catch(Exception e){Log.e(TAG,"Hardware registration failed",e);return null;}
 }
 public String onHardwareRemoved(TvInputHardwareInfo hw){
  TvInputInfo old=inputs.remove(hw.getDeviceId());hardwareInfos.remove(hw.getDeviceId());ports.values().remove(hw.getDeviceId());return old==null?null:old.getId();
 }
 @Override public Session onCreateSession(String inputId){
  for(Map.Entry<Integer,TvInputInfo> e:inputs.entrySet())if(e.getValue().getId().equals(inputId))return new HdmiSession(this,e.getKey(),e.getValue());
  for(TvInputInfo child:cecInputs.values())if(child.getId().equals(inputId)){
   for(Map.Entry<Integer,TvInputInfo> e:inputs.entrySet())if(e.getValue().getId().equals(child.getParentId()))return new HdmiSession(this,e.getKey(),e.getValue());
  }
  Log.e(TAG,"Unknown input requested");return null;
 }
 final class HdmiSession extends Session {
  final int device;final TvInputInfo info;final Handler handler=new Handler(Looper.getMainLooper());
  Object hardware;TvStreamConfig[] configs;Surface surface;float volume=0;boolean closed,tuned;
  volatile boolean attached;
  OriginalPanelRefresh panelRefresh;
  OriginalCec cec;
  void stopCec(){if(cec!=null){cec.close();cec=null;}if(cecOwner==this)cecOwner=null;}
  void startCec(){
   if(cecOwner!=null&&cecOwner!=this)cecOwner.stopCec();
   stopCec();cecOwner=this;
   try{cec=new OriginalCec(MainHdmiService.this,device-4,()->!closed&&tuned&&attached&&cecOwner==this);cec.select();}
   catch(Exception e){Log.w(TAG,"CEC client unavailable; keeping video",e);}
  }
  void stopRefresh(){attached=false;if(panelRefresh!=null){panelRefresh.close();panelRefresh=null;}}
  final TvInputManager.TvInputCallback inputCallback=new TvInputManager.TvInputCallback(){
   @Override public void onInputStateChanged(String id,int state){if(info.getId().equals(id))publishState();}
  };
  HdmiSession(Context context,int device,TvInputInfo info){
   super(context);this.device=device;this.info=info;
   TvInputManager.HardwareCallback cb=new TvInputManager.HardwareCallback(){
    public void onReleased(){handler.post(()->{stopCec();stopRefresh();hardware=null;leaveInputMode();notifyVideoUnavailable(0);});}
    public void onStreamConfigChanged(TvStreamConfig[] c){handler.post(()->{configs=c;attach();});}
   };
   try {
    Executor executor=handler::post;
    hardware=call(manager,"acquireTvInputHardware",new Class<?>[]{int.class,TvInputInfo.class,String.class,int.class,Executor.class,TvInputManager.HardwareCallback.class},device,info,null,400,executor,cb);
    call(manager,"registerCallback",new Class<?>[]{TvInputManager.TvInputCallback.class,Handler.class},inputCallback,handler);
    if(hardware==null)notifyVideoUnavailable(0);
   }catch(Exception e){Log.e(TAG,"Acquire failed",e);notifyVideoUnavailable(0);}
  }
  void publishState(){
   if(closed||!tuned||surface==null||hardware==null)return;
   try{int state=(Integer)call(manager,"getInputState",new Class<?>[]{String.class},info.getId());
    if(state==0)notifyVideoAvailable();else notifyVideoUnavailable(1);
   }catch(Exception e){Log.e(TAG,"Input state failed",e);notifyVideoUnavailable(0);}
  }
  boolean attach(){
   if(closed||hardware==null)return false;
   try{
    if(surface==null){stopCec();stopRefresh();call(hardware,"setSurface",new Class<?>[]{Surface.class,TvStreamConfig.class},null,null);return true;}
    if(!tuned||!modeOwners.contains(this))return false;
    if(configs==null)return false;
    TvStreamConfig selected=null;for(TvStreamConfig c:configs)if(c.getType()==1&&c.getStreamId()==0)selected=c;
    if(selected==null)return false;
    boolean ok=(Boolean)call(hardware,"setSurface",new Class<?>[]{Surface.class,TvStreamConfig.class},surface,selected);
    Log.i(TAG,"Attach device="+device+" result="+ok);
    if(ok){call(hardware,"setStreamVolume",new Class<?>[]{float.class},volume);attached=true;if(cec==null)startCec();if(panelRefresh==null)panelRefresh=new OriginalPanelRefresh(()->attached);publishState();}else{stopCec();stopRefresh();notifyVideoUnavailable(0);}
    return ok;
   }catch(Exception e){stopCec();stopRefresh();Log.e(TAG,"Surface failed",e);notifyVideoUnavailable(0);return false;}
  }
  @Override public boolean onSetSurface(Surface value){surface=value;return attach();}
  @Override public boolean onTune(Uri channel){
   if(closed||hardware==null)return false;
   try {
    int before=OriginalTvMode.set(false);
    modeOwners.add(this);tuned=true;
    Log.i(TAG,"Original source mode="+before+" -> 0; device="+device);
    notifyVideoUnavailable(3);attach();return true;
   }catch(Exception e){Log.e(TAG,"Original input mode failed; surface not attached",e);notifyVideoUnavailable(0);return false;}
  }
  void leaveInputMode(){
   if(!modeOwners.remove(this)||!modeOwners.isEmpty())return;
   try{int before=OriginalTvMode.set(true);Log.i(TAG,"Original source mode="+before+" -> 1; last session released");}
   catch(Exception e){Log.e(TAG,"Original application mode restore failed",e);}
  }
  @Override public void onSetStreamVolume(float value){
   volume=Math.max(0,Math.min(1,value));if(hardware!=null)try{call(hardware,"setStreamVolume",new Class<?>[]{float.class},volume);}catch(Exception e){Log.e(TAG,"Volume failed",e);}
  }
  @Override public void onSetCaptionEnabled(boolean enabled){}
  @Override public boolean onKeyDown(int key,KeyEvent event){boolean handled=cec!=null&&cec.keys.key(key,true);if(CecKeyGate.supported(key))Log.i(TAG,"CEC key down="+key+" handled="+handled);return handled;}
  @Override public boolean onKeyUp(int key,KeyEvent event){boolean handled=cec!=null&&cec.keys.key(key,false);if(CecKeyGate.supported(key))Log.i(TAG,"CEC key up="+key+" handled="+handled);return handled;}
  @Override public void onRelease(){
   stopCec();
   closed=true;handler.removeCallbacksAndMessages(null);
   stopRefresh();
   // Stock unregisterTvView restores application mode BEFORE TvView.reset.
   leaveInputMode();
   try{call(manager,"unregisterCallback",new Class<?>[]{TvInputManager.TvInputCallback.class},inputCallback);}catch(Exception e){Log.w(TAG,"Callback release",e);}
   if(hardware!=null){
    try{call(hardware,"setStreamVolume",new Class<?>[]{float.class},0f);call(hardware,"setSurface",new Class<?>[]{Surface.class,TvStreamConfig.class},null,null);}catch(Exception e){Log.e(TAG,"Detach failed",e);}
    try{call(manager,"releaseTvInputHardware",new Class<?>[]{int.class,hardware.getClass()},device,hardware);}catch(Exception e){Log.e(TAG,"Release failed",e);}
   }
   hardware=null;surface=null;Log.i(TAG,"Released device="+device);
  }
 }
}
