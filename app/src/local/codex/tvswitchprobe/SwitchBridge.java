// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import android.content.Context;
import android.os.IBinder;
import android.os.Parcel;
final class SwitchBridge {
    static boolean isGoogle(){try{return "1".equals(Class.forName("android.os.SystemProperties").getMethod("get",String.class).invoke(null,"ro.codex.fileboot"))&&android.os.Build.VERSION.SDK_INT>=34;}catch(Exception e){return false;}}
    static void returnToStock()throws Exception{
        if(!isGoogle())throw new IllegalStateException("Google runtime required");
        if(!invoke(3,"/system/bin/svc power reboot"))throw new IllegalStateException("Reboot request refused");
    }
    static boolean invoke(int code,String... values)throws Exception {
        IBinder b=(IBinder)Class.forName("android.os.ServiceManager").getMethod("getService",String.class).invoke(null,"TvService");
        if(b==null)throw new IllegalStateException("TvService unavailable");
        Parcel q=Parcel.obtain(),r=Parcel.obtain();
        try{q.writeInterfaceToken("mitv.internal.ITvService");for(String v:values)q.writeString(v);if(!b.transact(code,q,r,0))throw new IllegalStateException("Unsupported service");r.readException();return r.readInt()!=0;}finally{q.recycle();r.recycle();}
    }
    static void start(Context c,String action)throws Exception {
        boolean defaults=action.equals("auto-google")||action.equals("google-healthy")||action.equals("return-stock");
        if(!action.equals("check")&&!action.equals("enter-google")&&!(defaults&&AssetPins.ENABLE_PERSISTENT_DEFAULT))throw new IllegalArgumentException();
        String apk=c.getApplicationInfo().sourceDir;
        if(!apk.matches("[A-Za-z0-9/._~+=-]+"))throw new IllegalStateException("Unexpected APK path");
        String path=AssetPins.ENABLE_PERSISTENT_DEFAULT?DefaultStateFiles.DIR+"/worker.sh":"/data/local/codex-system-switch-v1.sh";
        String body="#!/system/bin/sh\nCLASSPATH="+apk+" app_process /system/bin local.codex.tvswitchprobe.SwitchWorker "+action+"\n";
        if(!invoke(2,path,body))throw new IllegalStateException("Helper file write refused");
        if(!invoke(3,"/system/bin/sh "+path))throw new IllegalStateException("Helper execution refused");
    }
}
