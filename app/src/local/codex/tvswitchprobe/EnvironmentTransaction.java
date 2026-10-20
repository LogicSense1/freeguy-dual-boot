// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import java.util.Arrays;

/** File-level protocol only. Caller must hold its exclusive lock through rollback. */
public final class EnvironmentTransaction {
    public interface IO { byte[] read(boolean main)throws Exception; void write(boolean main,byte[] data)throws Exception; void sync()throws Exception; }
    private final IO io;private final byte[] main,backup;private boolean mainAttempted,backupAttempted;
    public EnvironmentTransaction(IO io,byte[] main,byte[] backup){this.io=io;this.main=main.clone();this.backup=backup.clone();}
    private void verify(boolean which,byte[] wanted)throws Exception{if(!Arrays.equals(wanted,io.read(which)))throw new IllegalStateException("Environment readback mismatch");}
    private void put(boolean which,byte[] data)throws Exception{io.write(which,data);io.sync();verify(which,data);}
    public void commit(byte[] newMain,byte[] newBackup)throws Exception{
        if(mainAttempted||backupAttempted)throw new IllegalStateException("Transaction already attempted");
        verify(true,main);verify(false,backup);
        backupAttempted=true;put(false,newBackup);
        verify(true,main); // Refuse to overwrite a concurrent primary change.
        mainAttempted=true;put(true,newMain);
        verify(true,newMain);verify(false,newBackup);
    }
    public boolean attempted(){return mainAttempted||backupAttempted;}
    public void rollback()throws Exception{
        // Recover the selected primary first. Never rewrite an untouched primary.
        if(mainAttempted)put(true,main);
        if(backupAttempted)put(false,backup);
        verify(true,main);verify(false,backup);
        mainAttempted=false;backupAttempted=false;
    }
}
