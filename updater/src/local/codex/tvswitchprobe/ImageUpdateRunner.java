// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import android.system.*;
import java.io.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Inactive ordinary-file updates only. Permissions are provisioned separately.
 * No chmod, partition write, reboot, default selection, or deletion operation.
 */
public final class ImageUpdateRunner implements ImageTransaction.IO {
    static final String TAG="CodexImageUpdate";
    static final String STATE_DIR="/data/local/codex-system-default-v1";
    static final String LABEL="u:object_r:system_data_file:s0";
    private final Set<String> allowed=new HashSet<>();
    private final Map<String,String> memo=new HashMap<>();
    private final String boot;
    private int moves;
    private final int stopAfter;
    private ImageUpdateRunner(int stop)throws Exception {
        stopAfter=stop;boot=text("/proc/sys/kernel/random/boot_id");
        for(ImageTransaction.Row r:UpdatePins.ROWS)
            Collections.addAll(allowed,r.active,r.candidate,r.backup);
    }
    static void need(boolean b,String m){if(!b)throw new IllegalStateException(m);}
    static String text(String p)throws Exception{return new String(Files.readAllBytes(Paths.get(p)),StandardCharsets.US_ASCII).trim();}
    static String prop(String k)throws Exception{return (String)Class.forName("android.os.SystemProperties").getMethod("get",String.class).invoke(null,k);}
    static String label(String p)throws Exception{return (String)Class.forName("android.os.SELinux").getMethod("getFileContext",String.class).invoke(null,p);}
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format("%02x",b&255));return out.toString();}
    static StructStat stat(String p)throws Exception {
        try{return Os.lstat(p);}catch(ErrnoException e){if(e.errno==OsConstants.ENOENT)return null;throw e;}
    }
    static void directory(String p,int mode)throws Exception {
        StructStat s=stat(p);need(s!=null&&(s.st_mode&0177777)==(0040000|mode)&&s.st_uid==1000&&s.st_gid==1000&&LABEL.equals(label(p)),"Directory is not provisioned: "+p);
    }
    static void fsyncParent(String path)throws Exception {
        String p=new File(path).getParent();
        FileDescriptor fd=Os.open(p,OsConstants.O_RDONLY|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        try{need((Os.fstat(fd).st_mode&0170000)==0040000,"Not a directory");Os.fsync(fd);}finally{Os.close(fd);}
    }
    static DefaultBootPolicy readDefault()throws Exception {
        String path=STATE_DIR+"/state";StructStat s=stat(path);
        need(s!=null&&s.st_uid==1000&&s.st_gid==1000&&(s.st_mode&0177777)==0100600&&s.st_nlink==1&&s.st_size<=512,"Default state metadata changed");
        FileDescriptor fd=Os.open(path,OsConstants.O_RDONLY|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        try(FileInputStream in=new FileInputStream(fd)) {
            StructStat actual=Os.fstat(fd);need(actual.st_dev==s.st_dev&&actual.st_ino==s.st_ino,"Default state changed while opening");
            ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[513];int n;
            while((n=in.read(b))!=-1){out.write(b,0,n);need(out.size()<=512,"Default state grew");}
            return DefaultBootPolicy.decode(out.toByteArray());
        }
    }
    public void guard()throws Exception {
        need(Os.getuid()==1000&&"freeguy".equals(prop("ro.product.device"))&&"_b".equals(prop("ro.boot.slot_suffix"))&&"11".equals(prop("ro.build.version.release"))&&"1".equals(prop("sys.boot_completed"))&&"01.01.250225".equals(prop("ro.bootloader"))&&"green".equals(prop("ro.boot.verifiedbootstate"))&&!"1".equals(prop("ro.codex.fileboot")),"Verified stock B required");
        need(boot.equals(text("/proc/sys/kernel/random/boot_id")),"Boot identity changed");
        directory(STATE_DIR,0700);
        DefaultBootPolicy policy=readDefault();
        need(policy.target==DefaultBootPolicy.Target.XIAOMI&&policy.phase==DefaultBootPolicy.Phase.IDLE,"Select HyperOS before updating images");
        // The stock system hides other processes' proc entries. Inspect the
        // updater's own mount namespace, where all rename operations occur.
        String mounts=text("/proc/self/mounts");
        need(mounts.contains(" /data f2fs ")&&!mounts.contains("/loop")&&!mounts.contains("fileboot-host"),"Image mounts may be active");
        File[] blocks=new File("/sys/block").listFiles();need(blocks!=null,"Cannot inspect loop devices");
        for(File b:blocks)if(b.getName().startsWith("loop")) {
            File f=new File(b,"loop/backing_file");
            if(f.exists())need(text(f.toString()).isEmpty(),"Loop backing file still attached");
        }
        for(String p:UpdatePins.DIRECTORIES)directory(p,0750);
    }
    public String hash(String path)throws Exception {
        need(allowed.contains(path),"Path outside update plan");
        StructStat s=stat(path);if(s==null)return null;
        need((s.st_mode&0170000)==0100000&&s.st_nlink==1&&s.st_gid==1000&&
             (s.st_uid==0||s.st_uid==1000||s.st_uid==2000)&&s.st_size>=4096&&s.st_size<=2147483648L&&
             ((s.st_mode&07777)==0640||(s.st_mode&07777)==0750)&&LABEL.equals(label(path)),"Unexpected image metadata: "+path);
        // Renames preserve the inode and data. Cache only during this locked
        // invocation; clear it for a full end-of-operation byte verification.
        String key=s.st_dev+":"+s.st_ino+":"+s.st_size+":"+s.st_mtime+":"+s.st_mode+":"+s.st_uid;
        if(memo.containsKey(key))return memo.get(key);
        FileDescriptor fd=Os.open(path,OsConstants.O_RDONLY|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        MessageDigest digest=MessageDigest.getInstance("SHA-256");long count=0;
        try(FileInputStream in=new FileInputStream(fd)) {
            StructStat opened=Os.fstat(fd);need(opened.st_ino==s.st_ino&&opened.st_dev==s.st_dev,"File changed while opening");
            byte[] buf=new byte[1048576];int n;
            while((n=in.read(buf))!=-1){count+=n;need(count<=s.st_size,"Image grew");digest.update(buf,0,n);}
            StructStat end=Os.fstat(fd);need(count==s.st_size&&end.st_size==s.st_size&&end.st_mtime==s.st_mtime,"Image changed while hashing");
        }
        String h=hex(digest.digest());memo.put(key,h);return h;
    }
    public void move(String from,String to)throws Exception {
        need(allowed.contains(from)&&allowed.contains(to)&&stat(from)!=null&&stat(to)==null,"Invalid rename");
        StructStat old=Os.lstat(from);Os.rename(from,to);
        fsyncParent(from);fsyncParent(to);
        StructStat next=Os.lstat(to);need(stat(from)==null&&old.st_dev==next.st_dev&&old.st_ino==next.st_ino,"Rename identity changed");
        android.util.Log.i(TAG,"MOVE "+(++moves)+" plan="+UpdatePins.ID);
        if(stopAfter==moves)throw new InterruptedIOException("Requested stop after durable rename "+moves);
    }
    private static void stockEnvironment()throws Exception {
        Class<?> hp=Class.forName("android.os.HwParcel"),ib=Class.forName("android.os.IHwBinder");
        Object svc=Class.forName("android.os.HwBinder").getMethod("getService",String.class,String.class).invoke(null,"vendor.mediatek.tv.mtktvfactory@1.0::IMtkTvFApiSystem","default");
        need(svc!=null,"Factory read service unavailable");
        for(String path:new String[]{"/mnt/vendor/uenv/uboot.env","/mnt/vendor/uenv/uboot.env.bak"}) {
            Object req=hp.getConstructor().newInstance(),rep=hp.getConstructor().newInstance();
            try{
                hp.getMethod("writeInterfaceToken",String.class).invoke(req,"vendor.mediatek.tv.mtktvfactory@1.0::IMtkTvFApiSystem");
                hp.getMethod("writeString",String.class).invoke(req,path);hp.getMethod("writeInt32",int.class).invoke(req,65536);hp.getMethod("writeInt64",long.class).invoke(req,0L);
                ib.getMethod("transact",int.class,hp,hp,int.class).invoke(svc,20,req,rep,0);hp.getMethod("verifySuccess").invoke(rep);
                ArrayList<?> v=(ArrayList<?>)hp.getMethod("readInt8Vector").invoke(rep);
                need((Integer)hp.getMethod("readInt32").invoke(rep)==0&&v.size()==65536,"Environment read failed");
                byte[] bytes=new byte[65536];for(int i=0;i<bytes.length;i++)bytes[i]=(Byte)v.get(i);
                // Pure codec validates CRC and exact stock argument structure.
                byte[] trial=BootEnvironment.transform(bytes,true,"envdaily");
                need(Arrays.equals(bytes,BootEnvironment.transform(trial,false,"envdaily")),"Environment is not reversible stock configuration");
            }finally{hp.getMethod("release").invoke(req);hp.getMethod("release").invoke(rep);}
        }
    }
    public static void main(String[] args) {
        try{run(args);}catch(Throwable e){android.util.Log.e(TAG,"STOPPED plan="+UpdatePins.ID,e);}
    }
    private static void run(String[] args)throws Exception {
        need(args.length==1||args.length==2,"Action required");String action=args[0];
        need(action.equals("inspect")||action.equals("apply")||action.equals("rollback"),"Unsupported action");
        int stop=args.length==2?Integer.parseInt(args[1]):-1;
        need(args.length==1||!action.equals("inspect")&&stop>=1&&stop<=8,"Invalid interruption point");
        directory(STATE_DIR,0700);
        String lockPath=STATE_DIR+"/switch.lock";StructStat s=stat(lockPath);
        need(s!=null&&s.st_uid==1000&&s.st_gid==1000&&(s.st_mode&0177777)==0100600&&s.st_nlink==1,"Switch lock missing");
        FileDescriptor fd=Os.open(lockPath,OsConstants.O_RDWR|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        try(FileOutputStream stream=new FileOutputStream(fd);FileChannel channel=stream.getChannel();FileLock lock=channel.tryLock()) {
            need(lock!=null,"Switch or update already running");
            ImageUpdateRunner io=new ImageUpdateRunner(stop);io.guard();stockEnvironment();
            ImageTransaction transaction=new ImageTransaction(io,Arrays.asList(UpdatePins.ROWS));
            android.util.Log.i(TAG,"BEFORE "+transaction.inspect()+" plan="+UpdatePins.ID);
            if(action.equals("apply"))transaction.apply();else if(action.equals("rollback"))transaction.rollback();
            io.memo.clear();
            android.util.Log.i(TAG,"VERIFIED "+transaction.inspect()+" plan="+UpdatePins.ID);
            android.util.Log.i(TAG,"COMPLETE action="+action+" moves="+io.moves+" bootWrites=false dataWrites=false");
        }
    }
}
