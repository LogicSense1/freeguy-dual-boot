// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import android.system.Os;
import android.system.StructStat;
import java.io.*;
import java.nio.channels.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Fixed, reversible one-time boot selection. No block-device or firmware writes. */
public final class SwitchWorker {
    private static final String DIR="/data/vendor/codex-switch-proxy-v1";
    private static final String MAIN="/mnt/vendor/uenv/uboot.env", BACKUP=MAIN+".bak";
    private static final String PMAIN=DIR+"/main", PBACKUP=DIR+"/backup";
    private static Class<?> hp,ib; private static Object svc;
    private static String prop(String k)throws Exception{return (String)Class.forName("android.os.SystemProperties").getMethod("get",String.class).invoke(null,k);}
    private static void need(boolean b,String m){if(!b)throw new IllegalStateException(m);}
    static String hash(byte[] b)throws Exception {StringBuilder s=new StringBuilder();for(byte x:MessageDigest.getInstance("SHA-256").digest(b))s.append(String.format("%02x",x&255));return s.toString();}
    private static String fileHash(String path)throws Exception {
        MessageDigest d=MessageDigest.getInstance("SHA-256");byte[] buf=new byte[1048576];
        try(InputStream in=new FileInputStream(path)){int n;while((n=in.read(buf))!=-1)d.update(buf,0,n);}
        StringBuilder s=new StringBuilder();for(byte x:d.digest())s.append(String.format("%02x",x&255));return s.toString();
    }
    private static Object call(int code,String path,byte[] bytes)throws Exception {
        boolean snapshot=(DIR+"/last-stock-main.bin").equals(path)||(DIR+"/last-stock-backup.bin").equals(path);
        need(code==29||MAIN.equals(path)||BACKUP.equals(path)||PMAIN.equals(path)||PBACKUP.equals(path)||snapshot,"Unsupported path");
        need(code==20||code==23||code==24||code==29,"Unsupported operation");
        if(code==23)need(PMAIN.equals(path)||PBACKUP.equals(path)||snapshot,"Direct target write forbidden");
        if(code==24)need(snapshot,"Only owned snapshot permissions");
        Object req=hp.getConstructor().newInstance(),rep=hp.getConstructor().newInstance();
        try {
            hp.getMethod("writeInterfaceToken",String.class).invoke(req,"vendor.mediatek.tv.mtktvfactory@1.0::IMtkTvFApiSystem");
            if(code!=29)hp.getMethod("writeString",String.class).invoke(req,path);
            if(code==20){hp.getMethod("writeInt32",int.class).invoke(req,65536);hp.getMethod("writeInt64",long.class).invoke(req,0L);}
            if(code==23){need(bytes!=null&&bytes.length==65536,"Wrong length");ArrayList<Byte> v=new ArrayList<>();for(byte b:bytes)v.add(b);hp.getMethod("writeInt8Vector",ArrayList.class).invoke(req,v);hp.getMethod("writeString",String.class).invoke(req,snapshot?"wb":"r+b");}
            if(code==24)hp.getMethod("writeInt32",int.class).invoke(req,0600);
            ib.getMethod("transact",int.class,hp,hp,int.class).invoke(svc,code,req,rep,0);hp.getMethod("verifySuccess").invoke(rep);
            ArrayList<?> v=code==20?(ArrayList<?>)hp.getMethod("readInt8Vector").invoke(rep):null;
            int status=(Integer)hp.getMethod("readInt32").invoke(rep);need(status==0,"Factory file operation refused: "+code);
            if(v==null)return null;need(v.size()==65536,"Incomplete read");byte[] out=new byte[65536];for(int i=0;i<out.length;i++)out[i]=(Byte)v.get(i);return out;
        }finally{hp.getMethod("release").invoke(rep);hp.getMethod("release").invoke(req);}
    }
    private static byte[] read(String p)throws Exception{return (byte[])call(20,p,null);}
    private static void writeVerified(String p,byte[] b)throws Exception {
        call(23,p,b);call(29,null,null);need(Arrays.equals(b,read(p)),"Write readback mismatch");
    }
    private static void proxy()throws Exception {
        StructStat d=Os.lstat(DIR);need((d.st_mode&0177777)==0040750&&d.st_uid==0&&d.st_gid==1000,"Proxy directory metadata changed");
        String label=(String)Class.forName("android.os.SELinux").getMethod("getFileContext",String.class).invoke(null,DIR);
        need("u:object_r:vendor_cusdata_file:s0".equals(label),"Proxy context changed");
        for(String[] pair:new String[][]{{PMAIN,MAIN},{PBACKUP,BACKUP}}){
            StructStat l=Os.lstat(pair[0]),t=Os.stat(pair[0]),o=Os.lstat(pair[1]);
            need((l.st_mode&0170000)==0120000&&l.st_uid==0&&pair[1].equals(Os.readlink(pair[0])),"Proxy link changed");
            need(t.st_dev==o.st_dev&&t.st_ino==o.st_ino&&(o.st_mode&0170000)==0100000&&o.st_uid==0&&o.st_nlink==1&&o.st_size==65536,"Environment metadata changed");
        }
    }
    private static String normalized(String b)throws Exception {
        return BootArguments.normalized(b);
    }
    private static byte[][] preflight()throws Exception {
        need("freeguy".equals(prop("ro.product.device"))&&"_b".equals(prop("ro.boot.slot_suffix"))&&"1".equals(prop("sys.boot_completed"))&&"11".equals(prop("ro.build.version.release"))&&!"1".equals(prop("ro.codex.fileboot")),"Original HyperOS B required");
        need("green".equals(prop("ro.boot.verifiedbootstate"))&&"01.01.250225".equals(prop("ro.bootloader")),"Boot verification changed");
        hp=Class.forName("android.os.HwParcel");ib=Class.forName("android.os.IHwBinder");svc=Class.forName("android.os.HwBinder").getMethod("getService",String.class,String.class).invoke(null,"vendor.mediatek.tv.mtktvfactory@1.0::IMtkTvFApiSystem","default");
        need(svc!=null,"Factory service missing");proxy();
        byte[] m=read(MAIN),b=read(BACKUP);
        String kernelArgs=new String(Files.readAllBytes(Paths.get("/proc/cmdline")),"US-ASCII").trim();
        need(!kernelArgs.isEmpty()&&kernelArgs.length()<2048,"Current kernel arguments unavailable");
        for(byte[] src:new byte[][]{m,b}){
            String args=BootEnvironment.bootArguments(src);
            need(Arrays.asList(AssetPins.NORMALIZED_BOOTARGS).contains(hash(normalized(args).getBytes("US-ASCII"))),"Unsupported boot configuration");
            byte[] target=BootEnvironment.transform(src,true,AssetPins.INIT_NAME);need(Arrays.equals(src,BootEnvironment.transform(target,false,AssetPins.INIT_NAME)),"Nonreversible boot transform");
            need(BootEnvironment.bootArguments(target).length()+AssetPins.APPENDED_CMDLINE_BYTES+64<=2047,"Kernel command-line safety margin missing");
            need(kernelArgs.length()+BootEnvironment.bootArguments(target).length()-args.length()+64<=2047,"Measured kernel command-line safety margin missing");
        }
        android.util.Log.i("CodexSystemSwitch","Measured kernel command-line bytes="+kernelArgs.length()+" margin gate passed");
        for(int i=0;i<AssetPins.FILES.length;i++){
            progress("CHECKING",i,AssetPins.FILES.length);
            String[] row=AssetPins.FILES[i];StructStat st=Os.lstat(row[0]);need((st.st_mode&0170000)==0100000,"Asset is not a regular file");
            need(row[1].equals(fileHash(row[0])),"Code asset integrity mismatch: "+new File(row[0]).getName());
        }
        for(String path:AssetPins.MUTABLE){StructStat s=Os.lstat(path);need((s.st_mode&0170000)==0100000&&s.st_size>=4096,"Existing Google data missing");}
        need(Arrays.equals(m,read(MAIN))&&Arrays.equals(b,read(BACKUP)),"Configuration changed during checks");
        return new byte[][]{m,b};
    }
    private static void snapshot(String path,byte[] raw)throws Exception {
        try{StructStat st=Os.lstat(path);need((st.st_mode&0170000)==0100000&&st.st_uid==0&&st.st_nlink==1,"Snapshot path changed");}catch(android.system.ErrnoException e){if(e.errno!=android.system.OsConstants.ENOENT)throw e;}
        call(23,path,raw);call(24,path,null);call(29,null,null);need(Arrays.equals(raw,read(path)),"Snapshot readback failed");
    }
    private static void progress(String state,int n,int total){
        progress(state,n,total,"UNKNOWN");
    }
    private static void progress(String state,int n,int total,String error){
        android.util.Log.i("CodexSystemSwitch",state+" "+n+"/"+total);
        try{new ProcessBuilder("/system/bin/am","broadcast","--user","0","--receiver-foreground","-n","local.codex.tvswitchprobe/.SwitchResultReceiver","-a","local.codex.tvswitchprobe.RESULT","--es","state",state,"--ei","current",String.valueOf(n),"--ei","total",String.valueOf(total),"--es","error",SwitchFailure.safe(error)).redirectErrorStream(true).start().waitFor(5,TimeUnit.SECONDS);}catch(Exception ignored){}
    }
    private static String bootId()throws Exception {
        String id=new String(Files.readAllBytes(Paths.get("/proc/sys/kernel/random/boot_id")),"US-ASCII").trim();
        need(id.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),"Boot identity unavailable");return id;
    }
    private static void dailyGoogle()throws Exception {
        need(AssetPins.ENABLE_PERSISTENT_DEFAULT&&SwitchBridge.isGoogle()&&"1".equals(prop("sys.boot_completed")),"Daily Google runtime required");
        need(new String(Files.readAllBytes(Paths.get("/dev/codex-daily-v1")),"US-ASCII").equals("shared-default-v1\n"),"Shared default mount not established");
    }
    private static boolean recentHealth()throws Exception {
        long heartbeat=Long.parseLong(new String(Files.readAllBytes(Paths.get("/dev/codex-health-heartbeat")),"US-ASCII").trim());
        long now=android.os.SystemClock.uptimeMillis()/1000;
        return heartbeat>0&&heartbeat<=now&&now-heartbeat<=45;
    }
    static void awaitDailyHealth(java.util.function.BooleanSupplier stopped)throws Exception {
        dailyGoogle();long end=android.os.SystemClock.uptimeMillis()+180000,stable=-1;
        while(android.os.SystemClock.uptimeMillis()<end){
            need(!stopped.getAsBoolean(),"Boot job stopped");
            long now=android.os.SystemClock.uptimeMillis();
            // The app can read the public RAM heartbeat; the privileged worker
            // separately checks the private fault latch immediately before commit.
            boolean healthy=false;try{healthy=recentHealth();}catch(Exception ignored){}
            if(healthy){if(stable<0)stable=now;if(now-stable>=60000)return;}else stable=-1;
            Thread.sleep(2000);
        }
        throw new IllegalStateException("Google did not reach a stable health lease");
    }
    private static void reboot()throws Exception {
        progress("RESTARTING",14,14);
        Process reboot=new ProcessBuilder("/system/bin/svc","power","reboot").start();
        reboot.waitFor(15,TimeUnit.SECONDS);Thread.sleep(15000);
        throw new IllegalStateException("Reboot did not terminate helper");
    }
    public static void main(String[] args){
        try(RandomAccessFile lockFile=new RandomAccessFile(AssetPins.ENABLE_PERSISTENT_DEFAULT?DefaultStateFiles.DIR+"/switch.lock":"/data/local/codex-system-switch.lock","rw");FileChannel ch=lockFile.getChannel();FileLock lock=ch.tryLock()){
            need(lock!=null,"Switch already running");
            EnvironmentTransaction transaction=null;
            DefaultStateTransaction preferences=null;boolean preferenceAttempted=false;
            try {
                need(args.length==1,"Unsupported action");String action=args[0];
                boolean auto=action.equals("auto-google"),ack=action.equals("google-healthy"),back=action.equals("return-stock");
                need(action.equals("check")||action.equals("enter-google")||((auto||ack||back)&&AssetPins.ENABLE_PERSISTENT_DEFAULT),"Unsupported action");
                if(ack||back){
                    dailyGoogle();DefaultStateFiles files=new DefaultStateFiles();preferences=new DefaultStateTransaction(files);
                    DefaultBootPolicy current=preferences.read();
                    if(back){preferences.commit(current,DefaultBootPolicy.xiaomi());reboot();}
                    // Manual return or failure can supersede a delayed health report.
                    if(current.target==DefaultBootPolicy.Target.XIAOMI||current.phase==DefaultBootPolicy.Phase.PAUSED)return;
                    need(recentHealth()&&!files.faultLatched(),"Health changed before acknowledgement");
                    preferences.commit(current,current.googleHealthy(bootId(),current.attempt));
                    android.util.Log.i("CodexSystemSwitch","Google default acknowledged after stable health");return;
                }
                DefaultBootPolicy prior=null,next=null;DefaultStateFiles files=null;
                if(AssetPins.ENABLE_PERSISTENT_DEFAULT&&!action.equals("check")){
                    need(!SwitchBridge.isGoogle(),"Stock entry required");
                    need(Arrays.stream(AssetPins.FILES).anyMatch(row->row[0].equals("/data/local/tmp/rd-root/envdaily")),"Daily loader not pinned");
                    files=new DefaultStateFiles();preferences=new DefaultStateTransaction(files);prior=preferences.read();
                    if(auto){
                        DefaultBootPolicy.Decision d=prior.stockBoot(bootId(),UUID.randomUUID().toString(),files.faultLatched());
                        if(!d.enterGoogle){if(d.state!=prior)preferences.commit(prior,d.state);android.util.Log.i("CodexSystemSwitch","Automatic entry skipped: "+d.state.phase);return;}
                        next=d.state;
                    }else next=prior.selectGoogle(bootId(),UUID.randomUUID().toString());
                }
                byte[][] original=preflight();
                if(args[0].equals("check")){progress("READY",14,14);return;}
                need(AssetPins.ENABLE_ENTER_GOOGLE,"Entry not yet enabled");
                if(preferences!=null){
                    preferenceAttempted=true;preferences.commit(prior,next);
                    if(!auto)files.clearFaultAfterExplicitSelection();
                }
                snapshot(DIR+"/last-stock-main.bin",original[0]);snapshot(DIR+"/last-stock-backup.bin",original[1]);
                proxy();
                transaction=new EnvironmentTransaction(new EnvironmentTransaction.IO(){
                    public byte[] read(boolean main)throws Exception{return SwitchWorker.read(main?MAIN:BACKUP);}
                    public void write(boolean main,byte[] bytes)throws Exception{call(23,main?PMAIN:PBACKUP,bytes);}
                    public void sync()throws Exception{call(29,null,null);}
                },original[0],original[1]);
                transaction.commit(BootEnvironment.transform(original[0],true,AssetPins.INIT_NAME),BootEnvironment.transform(original[1],true,AssetPins.INIT_NAME));
                reboot();
            }catch(Throwable e){
                android.util.Log.e("CodexSystemSwitch","Switch stopped",e);
                if(transaction!=null&&transaction.attempted()){
                    try{transaction.rollback();progress("RESTORED",0,0,SwitchFailure.code(e));}
                    catch(Throwable recovery){android.util.Log.e("CodexSystemSwitch","Recovery required; do not reboot",recovery);progress("RECOVERY_REQUIRED",0,0);}
                }else progress("FAILED",0,0,SwitchFailure.code(e));
                if(preferenceAttempted&&preferences!=null){
                    try{DefaultBootPolicy s=preferences.read();preferences.commit(s,s.pause());}
                    catch(Throwable stateFailure){android.util.Log.e("CodexSystemSwitch","Default state remains unacknowledged",stateFailure);}
                }
            }
        }catch(Throwable e){android.util.Log.e("CodexSystemSwitch","Unable to acquire switch lock",e);progress("FAILED",0,0,SwitchFailure.code(e));}
    }
}
