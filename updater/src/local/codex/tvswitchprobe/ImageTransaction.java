// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.util.*;

/** Resume from file identities, including a stop between either pair of renames.
 * The caller excludes concurrent switches and checks the device before every move.
 * No deletion, content overwrite, boot operation or mutable-data entry is exposed.
 */
public final class ImageTransaction {
    public enum State { ORIGINAL, SAVED, INSTALLED }
    public static final class Row {
        public final String active, candidate, backup, oldHash, newHash;
        public Row(String a,String c,String b,String o,String n) {
            need(a!=null&&c!=null&&b!=null&&o!=null&&n!=null,"Null entry");
            need(!o.equals(n),"Unchanged input is not an update");
            active=a;candidate=c;backup=b;oldHash=o;newHash=n;
        }
    }
    public interface IO {
        /** null means absent; unexpected file types and unreadable files must throw. */
        String hash(String path)throws Exception;
        /** Refuse an occupied destination; rename, fsync both parents, then return. */
        void move(String from,String to)throws Exception;
        void guard()throws Exception;
    }
    private final IO io;
    private final List<Row> rows;
    static void need(boolean b,String m){if(!b)throw new IllegalStateException(m);}
    public ImageTransaction(IO io,List<Row> rows) {
        need(io!=null&&rows!=null&&!rows.isEmpty(),"Empty transaction");
        Set<String> names=new HashSet<>();
        for(Row r:rows)for(String p:new String[]{r.active,r.candidate,r.backup})
            need(names.add(p),"Overlapping transaction paths");
        this.io=io;this.rows=new ArrayList<>(rows);
    }
    public State state(Row r)throws Exception {
        String a=io.hash(r.active),c=io.hash(r.candidate),b=io.hash(r.backup);
        if(r.oldHash.equals(a)&&r.newHash.equals(c)&&b==null)return State.ORIGINAL;
        if(a==null&&r.newHash.equals(c)&&r.oldHash.equals(b))return State.SAVED;
        if(r.newHash.equals(a)&&c==null&&r.oldHash.equals(b))return State.INSTALLED;
        throw new IllegalStateException("Unrecognized file state; no guessed repair: "+r.active);
    }
    public List<State> inspect()throws Exception {
        io.guard();List<State> out=new ArrayList<>();
        for(Row r:rows)out.add(state(r));
        return out;
    }
    private void move(String from,String to,String hash)throws Exception {
        io.guard();need(hash.equals(io.hash(from))&&io.hash(to)==null,"Move identity changed");
        io.move(from,to);
        need(io.hash(from)==null&&hash.equals(io.hash(to)),"Move readback failed");
    }
    public void apply()throws Exception {
        inspect(); // Validate every row before changing any row.
        for(Row r:rows) {
            State s=state(r);
            if(s==State.ORIGINAL){move(r.active,r.backup,r.oldHash);s=state(r);}
            if(s==State.SAVED)move(r.candidate,r.active,r.newHash);
            need(state(r)==State.INSTALLED,"Incomplete installation");
        }
        inspect();
    }
    public void rollback()throws Exception {
        inspect();
        for(int i=rows.size()-1;i>=0;i--) {
            Row r=rows.get(i);State s=state(r);
            if(s==State.INSTALLED){move(r.active,r.candidate,r.newHash);s=state(r);}
            if(s==State.SAVED)move(r.backup,r.active,r.oldHash);
            need(state(r)==State.ORIGINAL,"Incomplete rollback");
        }
        inspect();
    }
}
