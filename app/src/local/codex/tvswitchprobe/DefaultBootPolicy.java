// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;

/** Persistent preference, distinct from the self-clearing boot environment.
 * Pure state transitions: no device I/O, pairing changes, or data recreation.
 */
public final class DefaultBootPolicy {
    public enum Target { XIAOMI, GOOGLE }
    public enum Phase { IDLE, PENDING, HEALTHY, PAUSED }
    private static final String NONE="-";
    public final Target target;
    public final Phase phase;
    public final String attempt, stockBoot, googleBoot;
    private static void require(boolean ok){if(!ok)throw new IllegalArgumentException("Invalid default-boot state");}
    private static boolean uuid(String s){return s!=null&&s.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");}
    private DefaultBootPolicy(Target t,Phase p,String a,String s,String g){
        require(t!=null&&p!=null);target=t;phase=p;attempt=a;stockBoot=s;googleBoot=g;
        if(t==Target.XIAOMI)require(p==Phase.IDLE&&NONE.equals(a)&&NONE.equals(s)&&NONE.equals(g));
        else {
            require(p!=Phase.IDLE&&uuid(a)&&uuid(s));
            require(p==Phase.PENDING?NONE.equals(g):p==Phase.HEALTHY?uuid(g):NONE.equals(g)||uuid(g));
            require(!s.equals(g));
        }
    }
    public static DefaultBootPolicy xiaomi(){return new DefaultBootPolicy(Target.XIAOMI,Phase.IDLE,NONE,NONE,NONE);}
    public DefaultBootPolicy selectGoogle(String currentStockBoot,String newAttempt){
        require(uuid(currentStockBoot)&&uuid(newAttempt)&&!newAttempt.equals(attempt));
        return new DefaultBootPolicy(Target.GOOGLE,Phase.PENDING,newAttempt,currentStockBoot,NONE);
    }
    public DefaultBootPolicy googleHealthy(String currentGoogleBoot,String expectedAttempt){
        require(uuid(currentGoogleBoot)&&attempt.equals(expectedAttempt)&&!stockBoot.equals(currentGoogleBoot));
        if(phase==Phase.HEALTHY){require(googleBoot.equals(currentGoogleBoot));return this;}
        require(target==Target.GOOGLE&&phase==Phase.PENDING);
        return new DefaultBootPolicy(target,Phase.HEALTHY,attempt,stockBoot,currentGoogleBoot);
    }
    public DefaultBootPolicy pause(){
        return target==Target.XIAOMI?this:new DefaultBootPolicy(target,Phase.PAUSED,attempt,stockBoot,googleBoot);
    }
    public static final class Decision {
        public final DefaultBootPolicy state;
        public final boolean enterGoogle;
        private Decision(DefaultBootPolicy s,boolean e){state=s;enterGoogle=e;}
    }
    /** Called under the same lock as the subsequent environment transaction.
     * PENDING must be durable before any boot-environment write or reboot.
     * A failed attempt is not retried automatically on a different stock boot.
     */
    public Decision stockBoot(String currentBoot,String newAttempt,boolean faultLatched){
        require(uuid(currentBoot)&&uuid(newAttempt));
        if(target==Target.XIAOMI)return new Decision(this,false);
        if(faultLatched)return new Decision(pause(),false);
        if(phase==Phase.PENDING)return new Decision(stockBoot.equals(currentBoot)?this:pause(),false);
        if(phase==Phase.PAUSED||stockBoot.equals(currentBoot))return new Decision(this,false);
        require(phase==Phase.HEALTHY&&!googleBoot.equals(currentBoot));
        return new Decision(selectGoogle(currentBoot,newAttempt),true);
    }
    private String body(){return "CTV_DEFAULT_V1\n"+target+"\n"+phase+"\n"+attempt+"\n"+stockBoot+"\n"+googleBoot+"\n";}
    private static String sha(byte[] b){
        try{StringBuilder s=new StringBuilder();for(byte x:MessageDigest.getInstance("SHA-256").digest(b))s.append(String.format("%02x",x&255));return s.toString();}
        catch(Exception e){throw new IllegalStateException(e);}
    }
    public byte[] encode(){String s=body();return (s+sha(s.getBytes(StandardCharsets.US_ASCII))+"\n").getBytes(StandardCharsets.US_ASCII);}
    public static DefaultBootPolicy decode(byte[] bytes){
        require(bytes!=null&&bytes.length>=80&&bytes.length<=512);
        String[] p=new String(bytes,StandardCharsets.US_ASCII).split("\n",-1);
        require(p.length==8&&p[0].equals("CTV_DEFAULT_V1")&&p[7].isEmpty());
        DefaultBootPolicy s=new DefaultBootPolicy(Target.valueOf(p[1]),Phase.valueOf(p[2]),p[3],p[4],p[5]);
        require(Arrays.equals(s.encode(),bytes));return s;
    }
}
