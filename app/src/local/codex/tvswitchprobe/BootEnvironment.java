// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.CRC32;

/** Pure codec for a single exact trial. No file, Binder, shell, or device I/O. */
public final class BootEnvironment {
    private static final int SIZE = 65536;
    private static final String[] STOCK = {"root=/dev/ram", "init=/init", "rw", "loglevel=0"};
    private static final String[] EXTRA = {"rdinit=/codex-no-rdinit", "rootfstype=f2fs", "rootflags=norecovery"};
    private static void require(boolean ok) { if (!ok) throw new IllegalArgumentException("Environment does not match exact supported state"); }
    private static long crc(byte[] raw) { CRC32 c=new CRC32(); c.update(raw,4,SIZE-4); return c.getValue(); }
    private static List<byte[]> parse(byte[] raw) {
        require(raw.length==SIZE);
        long expected=0;for(int i=0;i<4;i++)expected|=((long)raw[i]&255)<<(i*8);
        require(crc(raw)==expected);
        List<byte[]> entries=new ArrayList<>(); Set<String> keys=new HashSet<>();
        int pos=4;
        while(pos<SIZE-1 && raw[pos]!=0) {
            int start=pos, split=-1;
            while(pos<SIZE && raw[pos]!=0){ if(raw[pos]=='='&&split<0)split=pos;pos++; }
            require(pos<SIZE && split>start && entries.size()<1024);
            require(keys.add(new String(raw,start,split-start,StandardCharsets.ISO_8859_1)));
            entries.add(Arrays.copyOfRange(raw,start,pos));pos++;
        }
        require(pos<SIZE-1 && raw[pos]==0 && raw[pos+1]==0 && keys.contains("bootargs"));
        for(int i=pos;i<SIZE;i++)require(raw[i]==0);
        return entries;
    }
    public static String bootArguments(byte[] source) {
        for(byte[] e:parse(source)) {
            String s=new String(e,StandardCharsets.ISO_8859_1);
            if(s.startsWith("bootargs="))return s.substring(9);
        }
        throw new IllegalArgumentException("Missing bootargs");
    }
    public static byte[] transform(byte[] source, boolean install) {
        return transform(source,install,"envwifi");
    }
    public static byte[] transform(byte[] source, boolean install,String loader) {
        require(loader.equals("envwifi")||loader.equals("envdaily"));
        String[] trial={"root=/dev/mmcblk0p42","init=/local/tmp/rd-root/"+loader,"ro","loglevel=7"};
        List<byte[]> entries=parse(source); int selected=-1; String value=null;
        for(int i=0;i<entries.size();i++) {
            String s=new String(entries.get(i),StandardCharsets.ISO_8859_1);
            if(s.startsWith("bootargs=")){selected=i;value=s.substring(9);}
        }
        require(value!=null && !value.isEmpty() && value.length()<=2047);
        List<String> tokens=new ArrayList<>(Arrays.asList(value.split(" ",-1)));
        for(String t:tokens){ require(!t.isEmpty());for(int i=0;i<t.length();i++)require(t.charAt(i)>=33&&t.charAt(i)<=126); }
        String[] from=install?STOCK:trial, to=install?trial:STOCK;
        for(int i=0;i<from.length;i++)require(Collections.frequency(tokens,from[i])==1 && !tokens.contains(to[i]));
        for(String prefix:new String[]{"root=","init=","loglevel=","rdinit=","rootfstype=","rootflags="}) {
            int count=0;for(String t:tokens)if(t.startsWith(prefix))count++;
            boolean optional=prefix.equals("rdinit=")||prefix.equals("rootfstype=")||prefix.equals("rootflags=");
            require(count==((optional&&install)?0:1));
        }
        for(int i=0;i<from.length;i++)tokens.set(tokens.indexOf(from[i]),to[i]);
        for(String t:EXTRA){if(install)tokens.add(t);else{require(Collections.frequency(tokens,t)==1);tokens.remove(t);}}
        String result=String.join(" ",tokens);require(result.length()<=2047);
        entries.set(selected,("bootargs="+result).getBytes(StandardCharsets.ISO_8859_1));
        ByteArrayOutputStream body=new ByteArrayOutputStream();
        for(byte[] entry:entries){body.write(entry,0,entry.length);body.write(0);}body.write(0);
        require(body.size()<=SIZE-4);
        byte[] out=new byte[SIZE];System.arraycopy(body.toByteArray(),0,out,4,body.size());
        long checksum=crc(out);for(int i=0;i<4;i++)out[i]=(byte)(checksum>>>(8*i));
        parse(out);
        return out;
    }
}
