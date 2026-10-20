// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import android.system.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Fixed ordinary-file store. Caller holds the switch lock for its whole operation.
 * Google must receive this exact directory from the stock-data mount; it must
 * never create a separate directory and mistake it for the shared preference.
 */
final class DefaultStateFiles implements DefaultStateTransaction.IO {
    static final String DIR="/data/local/codex-system-default-v1";
    static final byte[] OK="OK\n".getBytes(StandardCharsets.US_ASCII);
    private static void need(boolean b){if(!b)throw new IllegalStateException("Default-file metadata or I/O mismatch");}
    private static StructStat stat(String p)throws Exception {
        try{return Os.lstat(p);}catch(ErrnoException e){if(e.errno==OsConstants.ENOENT)return null;throw e;}
    }
    private static void directory()throws Exception {
        StructStat s=stat(DIR);need(s!=null&&s.st_uid==1000&&s.st_gid==1000&&(s.st_mode&0177777)==0040700);
    }
    static void initializeStock()throws Exception {
        need(android.os.Process.myUid()==1000&&!SwitchBridge.isGoogle());
        // Created once by the validated Google-root provisioning path. Stock
        // system_app cannot mkdir here; do not relax SELinux to work around it.
        directory();
        DefaultStateFiles files=new DefaultStateFiles();
        if(files.read()==null){
            // With no preference, HyperOS is the only permitted default.
            files.replaceNamed("fault",OK);
            files.replace(DefaultBootPolicy.xiaomi().encode());
        }
    }
    private static void regular(StructStat s)throws Exception {
        need(s!=null&&s.st_uid==1000&&s.st_gid==1000&&(s.st_mode&0177777)==0100600&&s.st_nlink==1&&s.st_size<=512);
    }
    private byte[] readNamed(String name)throws Exception {
        directory();String p=DIR+"/"+name;StructStat before=stat(p);if(before==null)return null;regular(before);
        FileDescriptor fd=Os.open(p,OsConstants.O_RDONLY|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        try(FileInputStream in=new FileInputStream(fd)){
            StructStat actual=Os.fstat(fd);regular(actual);need(actual.st_ino==before.st_ino&&actual.st_dev==before.st_dev);
            ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[513];int n;
            while((n=in.read(b))!=-1){out.write(b,0,n);need(out.size()<=512);}
            need(out.size()==actual.st_size);return out.toByteArray();
        }
    }
    private void replaceNamed(String name,byte[] bytes)throws Exception {
        need(name.equals("state")||name.equals("fault"));need(bytes.length>0&&bytes.length<=512);directory();
        String p=DIR+"/"+name,tmp=p+".tmp";StructStat old=stat(p),stale=stat(tmp);
        if(old!=null)regular(old);if(stale!=null)regular(stale);
        FileDescriptor fd=Os.open(tmp,OsConstants.O_WRONLY|OsConstants.O_CREAT|OsConstants.O_TRUNC|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0600);
        try(FileOutputStream out=new FileOutputStream(fd)){regular(Os.fstat(fd));out.write(bytes);out.flush();Os.fsync(fd);}
        Os.rename(tmp,p);
        FileDescriptor parent=Os.open(DIR,OsConstants.O_RDONLY|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0);
        try{need((Os.fstat(parent).st_mode&0170000)==0040000);Os.fsync(parent);}finally{Os.close(parent);}
        need(Arrays.equals(bytes,readNamed(name)));
    }
    public byte[] read()throws Exception{return readNamed("state");}
    public void replace(byte[] bytes)throws Exception {DefaultBootPolicy.decode(bytes);replaceNamed("state",bytes);}
    boolean faultLatched()throws Exception{return !Arrays.equals(OK,readNamed("fault"));}
    void clearFaultAfterExplicitSelection()throws Exception{replaceNamed("fault",OK);}
}
