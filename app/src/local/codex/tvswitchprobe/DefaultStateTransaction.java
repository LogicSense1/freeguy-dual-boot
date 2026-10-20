// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.util.Arrays;

/** Ordinary preference-file durability boundary. Never writes boot partitions.
 * IO.replace must flush the temporary file, rename atomically, then fsync its
 * parent directory. A failure must prevent entry, even if the rename succeeded.
 */
public final class DefaultStateTransaction {
    public interface IO {
        byte[] read()throws Exception;
        void replace(byte[] bytes)throws Exception;
    }
    private final IO io;
    public DefaultStateTransaction(IO io){this.io=io;}
    public DefaultBootPolicy read()throws Exception{
        byte[] b=io.read();return b==null?DefaultBootPolicy.xiaomi():DefaultBootPolicy.decode(b);
    }
    public void commit(DefaultBootPolicy expected,DefaultBootPolicy next)throws Exception{
        if(!Arrays.equals(read().encode(),expected.encode()))throw new IllegalStateException("Default changed concurrently");
        byte[] b=next.encode();io.replace(b);
        byte[] found=io.read();
        if(found==null||!Arrays.equals(found,b))throw new IllegalStateException("Default readback failed");
        DefaultBootPolicy.decode(found);
    }
}
