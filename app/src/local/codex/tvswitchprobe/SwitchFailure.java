// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
/** Stable UI categories; never send raw exceptions or private paths to the UI. */
public final class SwitchFailure {
    public static String code(Throwable e){
        for(int i=0;e!=null&&i<8;i++,e=e.getCause()){
            String m=e.getMessage();
            if(m==null)continue;
            if(m.equals("Unsupported boot reason"))return "BOOT_REASON";
            if(m.equals("Switch already running"))return "BUSY";
            if(m.startsWith("Code asset integrity")||m.equals("Asset is not a regular file"))return "FILES";
            if(m.equals("Existing Google data missing"))return "DATA";
            if(m.contains("boot configuration")||m.contains("bootarg")||m.contains("boot fields")||
               m.contains("brightness")||m.contains("Boot verification")||m.contains("boot transform")||
               m.contains("command-line")||m.contains("kernel arguments"))return "CONFIG";
            if(m.contains("Proxy")||m.contains("Environment metadata"))return "ACCESS";
            if(m.contains("TvService")||m.contains("Factory service")||m.contains("Helper ")||
               m.contains("Unsupported service")||m.contains("Factory file operation"))return "SERVICE";
            if(m.contains("health")||m.contains("Health"))return "HEALTH";
            if(m.contains("Default")||m.contains("default")||m.contains("Shared"))return "DEFAULT";
            if(m.contains("Original HyperOS")||m.contains("Stock entry")||m.contains("Google runtime"))return "SYSTEM";
            if(m.contains("readback")||m.contains("Incomplete read")||m.contains("Configuration changed"))return "IO";
        }
        return "UNKNOWN";
    }
    public static String safe(String code){
        return code!=null&&code.matches("BOOT_REASON|BUSY|FILES|DATA|CONFIG|ACCESS|SERVICE|HEALTH|DEFAULT|SYSTEM|IO")?code:"UNKNOWN";
    }
}
