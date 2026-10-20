// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;

/** Exact original SourceManager enter/leave operation; no arbitrary keys/values. */
final class OriginalTvMode {
 static final String IFACE="vendor.mediatek.api.mtktvapi@1.0::IMtkTvApiConfig";
 static final String KEY="g_misc__av_cond_mmp_mode";
 static Object invoke(Object object,String method,Class<?>[] types,Object... args)throws Exception {
  return object.getClass().getMethod(method,types).invoke(object,args);
 }
 static int set(boolean applicationMode)throws Exception {
  Class<?> props=Class.forName("android.os.SystemProperties");
  if(!"1".equals(props.getMethod("get",String.class).invoke(null,"ro.codex.fileboot"))||
     !"14".equals(props.getMethod("get",String.class).invoke(null,"ro.build.version.release")))
   throw new SecurityException("Google RAM trial required");
  Class<?> hb=Class.forName("android.os.HwBinder"),ib=Class.forName("android.os.IHwBinder"),hp=Class.forName("android.os.HwParcel");
  Object service=hb.getMethod("getService",String.class,String.class,boolean.class).invoke(null,IFACE,"default",false);
  if(service==null)throw new IllegalStateException("Original config service missing");
  int before=read(service,ib,hp),target=applicationMode?1:0;
  if(before==target)return before;
  Object req=hp.getConstructor().newInstance(),rep=hp.getConstructor().newInstance();
  try {
   header(req,hp);
   hp.getMethod("writeInt32",int.class).invoke(req,target);
   hp.getMethod("writeInt16",short.class).invoke(req,(short)3);
   ib.getMethod("transact",int.class,hp,hp,int.class).invoke(service,3,req,rep,0);
   hp.getMethod("verifySuccess").invoke(rep);
   int rc=(Integer)hp.getMethod("readInt32").invoke(rep);
   if(rc!=0)throw new IllegalStateException("Original mode setter status="+rc);
  }finally{hp.getMethod("release").invoke(req);hp.getMethod("release").invoke(rep);}
  if(read(service,ib,hp)!=target)throw new IllegalStateException("Original mode readback mismatch");
  return before;
 }
 static void header(Object req,Class<?> hp)throws Exception {
  hp.getMethod("writeInterfaceToken",String.class).invoke(req,IFACE);
  hp.getMethod("writeInt16",short.class).invoke(req,(short)-1);
  hp.getMethod("writeString",String.class).invoke(req,KEY);
 }
 static int read(Object service,Class<?> ib,Class<?> hp)throws Exception {
  Object req=hp.getConstructor().newInstance(),rep=hp.getConstructor().newInstance();
  try {
   header(req,hp);
   ib.getMethod("transact",int.class,hp,hp,int.class).invoke(service,2,req,rep,0);
   hp.getMethod("verifySuccess").invoke(rep);
   int value=(Integer)hp.getMethod("readInt32").invoke(rep),rc=(Integer)hp.getMethod("readInt32").invoke(rep);
   if(rc!=0||(value!=0&&value!=1))throw new IllegalStateException("Invalid original source mode status/value");
   return value;
  }finally{hp.getMethod("release").invoke(req);hp.getMethod("release").invoke(rep);}
 }
}
