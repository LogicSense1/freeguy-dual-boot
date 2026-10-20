// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;

import android.util.Log;
import java.util.concurrent.*;
import java.util.function.BooleanSupplier;

/** Original read-only timing plus the physically verified current-value update. */
final class OriginalPanelRefresh implements AutoCloseable {
 static final String INPUT="vendor.mediatek.tv.mtktvfactory@1.0::IMtkTvFApiInputSource";
 static final String CONFIG="vendor.mediatek.api.mtktvapi@1.0::IMtkTvApiConfig";
 static final String KEY="g_disp__disp_back_light";
 final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"OriginalHdmiSignal");t.setDaemon(true);return t;});
 final SignalRefreshGate gate=new SignalRefreshGate();
 final BooleanSupplier active;
 volatile boolean closed;
 int errors;
 OriginalPanelRefresh(BooleanSupplier active){this.active=active;worker.scheduleWithFixedDelay(this::poll,0,1000,TimeUnit.MILLISECONDS);}
 boolean current(){return !closed&&active.getAsBoolean();}
 void poll(){
  if(!current())return;
  try {
   guard();int[] t=timing();if(!current())return;
   if(gate.observe(t[0],t[1],t[2],System.nanoTime()/1000000L)){
    int value=refresh();
    gate.markApplied();
    Log.i("OriginalHdmi","Stable timing "+t[0]+"x"+t[1]+" rawFps="+t[2]+"; original backlight UPDATE_ONLY="+value);
   }
   errors=0;
  }catch(Exception e){
   Log.e("OriginalHdmi","Original timing/backlight refresh unavailable",e);
   if(++errors>=3)close();
  }
 }
 static void guard()throws Exception {
  Class<?> p=Class.forName("android.os.SystemProperties");
  if(!"1".equals(p.getMethod("get",String.class).invoke(null,"ro.codex.fileboot"))||
     !"14".equals(p.getMethod("get",String.class).invoke(null,"ro.build.version.release")))throw new SecurityException("Google RAM trial only");
 }
 static Object service(String name)throws Exception {
  Object s=Class.forName("android.os.HwBinder").getMethod("getService",String.class,String.class,boolean.class).invoke(null,name,"default",false);
  if(s==null)throw new IllegalStateException("Original HAL unavailable");return s;
 }
 static int[] timing()throws Exception {
  Class<?> p=Class.forName("android.os.HwParcel"),b=Class.forName("android.os.HwBlob"),i=Class.forName("android.os.IHwBinder");
  Object q=p.getConstructor().newInstance(),r=p.getConstructor().newInstance();
  try{
   p.getMethod("writeInterfaceToken",String.class).invoke(q,INPUT);
   i.getMethod("transact",int.class,p,p,int.class).invoke(service(INPUT),14,q,r,0);
   p.getMethod("verifySuccess").invoke(r);Object blob=p.getMethod("readBuffer",long.class).invoke(r,16L);
   int[] t=new int[3];for(int n=0;n<3;n++)t[n]=(Integer)b.getMethod("getInt32",long.class).invoke(blob,(long)n*4);
   if((Integer)p.getMethod("readInt32").invoke(r)!=0)throw new IllegalStateException("Timing read failed");return t;
  }finally{p.getMethod("release").invoke(q);p.getMethod("release").invoke(r);}
 }
 static void header(Class<?> p,Object q)throws Exception {
  p.getMethod("writeInterfaceToken",String.class).invoke(q,CONFIG);
  p.getMethod("writeInt16",short.class).invoke(q,(short)-1);
  p.getMethod("writeString",String.class).invoke(q,KEY);
 }
 static int brightness(Class<?> p,Class<?> i,Object s)throws Exception {
  Object q=p.getConstructor().newInstance(),r=p.getConstructor().newInstance();
  try{
   header(p,q);i.getMethod("transact",int.class,p,p,int.class).invoke(s,2,q,r,0);p.getMethod("verifySuccess").invoke(r);
   int v=(Integer)p.getMethod("readInt32").invoke(r),rc=(Integer)p.getMethod("readInt32").invoke(r);
   if(rc!=0||v<1||v>100)throw new IllegalStateException("Invalid original brightness");return v;
  }finally{p.getMethod("release").invoke(q);p.getMethod("release").invoke(r);}
 }
 int refresh()throws Exception {
  Class<?> p=Class.forName("android.os.HwParcel"),i=Class.forName("android.os.IHwBinder");Object s=service(CONFIG);
  int value=brightness(p,i,s);
  if(!current())throw new IllegalStateException("Session ended before update");
  Object q=p.getConstructor().newInstance(),r=p.getConstructor().newInstance();
  try{
   header(p,q);p.getMethod("writeInt32",int.class).invoke(q,value);p.getMethod("writeInt16",short.class).invoke(q,(short)2);
   i.getMethod("transact",int.class,p,p,int.class).invoke(s,3,q,r,0);p.getMethod("verifySuccess").invoke(r);
   if((Integer)p.getMethod("readInt32").invoke(r)!=0)throw new IllegalStateException("Original update failed");
  }finally{p.getMethod("release").invoke(q);p.getMethod("release").invoke(r);}
  if(brightness(p,i,s)!=value)throw new IllegalStateException("Stored brightness changed");return value;
 }
 @Override public void close(){closed=true;worker.shutdownNow();}
}
