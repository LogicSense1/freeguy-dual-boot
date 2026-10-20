// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import java.util.*;

/** Normalize only the OEM fields observed to change between ordinary boots. */
public final class BootArguments {
    private static void need(boolean ok,String message){
        if(!ok)throw new IllegalStateException(message);
    }
    public static String normalized(String value){
        need(value!=null&&!value.isEmpty()&&value.length()<2048,"Invalid bootargs length");
        for(char c:value.toCharArray())need(c>=32&&c<127,"Non-ASCII bootargs");
        List<String> out=new ArrayList<>();Set<String> keys=new HashSet<>();
        int reasons=0,brightness=0;
        for(String t:value.split(" ",-1)){
            need(!t.isEmpty(),"Unexpected bootargs whitespace");
            int eq=t.indexOf('=');
            if(eq>=0)need(keys.add(t.substring(0,eq)),"Duplicate bootarg key");
            if(t.startsWith("androidboot.bootreason=")){
                need(t.equals("androidboot.bootreason=cold")||
                     t.equals("androidboot.bootreason=reboot")||
                     t.equals("androidboot.bootreason=warm"),"Unsupported boot reason");
                reasons++;
            }else if(t.startsWith("lastblval=")){
                need(t.matches("lastblval=[0-9]{1,3}")&&
                     Integer.parseInt(t.substring(10))<=255,"Invalid brightness");
                brightness++;
            }else out.add(t);
        }
        need(reasons==1&&brightness==1,"Dynamic boot fields missing");
        return String.join(" ",out);
    }
}
