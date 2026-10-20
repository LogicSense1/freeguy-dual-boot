// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
import android.content.Context;
import android.util.Log;
import java.lang.reflect.*;
import java.util.List;
import java.util.function.BooleanSupplier;

/** Standard framework client; same route/key API used by the OEM TV input service. */
final class OriginalCec implements CecKeyGate.Target {
 private static final String TAG="OriginalCec";
 private final int port;
 private final Object client;
 private final BooleanSupplier visible;
 private boolean closed;
 final CecKeyGate keys;
 private static Object call(Object object,String method,Class<?>[] types,Object... args)throws Exception {
  return object.getClass().getMethod(method,types).invoke(object,args);
 }
 OriginalCec(Context context,int port,BooleanSupplier visible)throws Exception {
  this.port=port;this.visible=visible;
  Object manager=context.getSystemService("hdmi_control");
  if(manager==null)throw new IllegalStateException("HDMI control unavailable");
  client=call(manager,"getTvClient",new Class<?>[0]);
  if(client==null)throw new IllegalStateException("TV CEC client unavailable");
  keys=new CecKeyGate(this);
 }
 void select(){
  if(closed)return;
  try {
   // Prefer a directly attached CEC device; otherwise select the physical port.
   int logical=-1;
   for(Object device:(List<?>)call(client,"getDeviceList",new Class<?>[0])) {
    int physical=(Integer)call(device,"getPhysicalAddress",new Class<?>[0]);
    int address=(Integer)call(device,"getLogicalAddress",new Class<?>[0]);
    if(physical==(port<<12)&&address>0&&address<15){logical=address;break;}
   }
   Class<?> callback=Class.forName("android.hardware.hdmi.HdmiTvClient$SelectCallback");
   Object listener=Proxy.newProxyInstance(callback.getClassLoader(),new Class<?>[]{callback},(proxy,method,args)->{
    if(method.getName().equals("onComplete")){Log.i(TAG,"Select port="+port+" result="+args[0]+" closed="+closed);return null;}
    if(method.getName().equals("toString"))return "CEC select callback";
    if(method.getName().equals("hashCode"))return System.identityHashCode(proxy);
    if(method.getName().equals("equals"))return proxy==args[0];
    return null;
   });
   call(client,logical>=0?"deviceSelect":"portSelect",new Class<?>[]{int.class,callback},logical>=0?logical:port,listener);
   Log.i(TAG,"Selection requested port="+port+" logical="+logical);
  }catch(Exception e){Log.w(TAG,"CEC selection unavailable; video remains independent",e);}
 }
 @Override public boolean matches(){
  if(closed||!visible.getAsBoolean())return false;
  try {
   Object device=call(client,"getActiveSource",new Class<?>[0]);
   return device!=null&&(Boolean)call(device,"isCecDevice",new Class<?>[0])&&
    (Integer)call(device,"getPortId",new Class<?>[0])==port;
  }catch(Exception e){return false;}
 }
 @Override public void send(int key,boolean down)throws Exception {
  call(client,"sendKeyEvent",new Class<?>[]{int.class,boolean.class},key,down);
  Log.i(TAG,"Forward port="+port+" key="+key+" down="+down);
 }
 void close(){keys.close();closed=true;}
}
