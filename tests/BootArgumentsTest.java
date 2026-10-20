// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
public final class BootArgumentsTest {
    private static final String BASE="root=/dev/ram init=/init rw loglevel=0";
    private static void reject(String value){
        try{BootArguments.normalized(value);throw new AssertionError("Accepted invalid arguments");}
        catch(IllegalStateException expected){}
    }
    public static void main(String[] args){
        for(String reason:new String[]{"cold","reboot","warm"})
            for(String level:new String[]{"0","87","255"}){
                String original=BASE+" androidboot.bootreason="+reason+" lastblval="+level;
                assert BootArguments.normalized(original).equals(BASE);
                // Normalization must not erase unrelated changes; pinned hash still detects them.
                assert !BootArguments.normalized(original+" unexpected=1").equals(BASE);
            }
        reject(BASE+" androidboot.bootreason=unknown lastblval=87");
        reject(BASE+" androidboot.bootreason=warm lastblval=256");
        reject(BASE+" androidboot.bootreason=warm lastblval=-1");
        reject(BASE+" androidboot.bootreason=warm lastblval=87 lastblval=87");
        reject(BASE+" androidboot.bootreason=warm androidboot.bootreason=cold lastblval=87");
        reject(BASE+" androidboot.bootreason=warm");
        reject(BASE+" lastblval=87");
        reject(BASE+"  androidboot.bootreason=warm lastblval=87");
        reject(BASE+" androidboot.bootreason=warm lastblval=87 ");
        reject(BASE+" androidboot.bootreason=warm lastblval=87 init=/other");
        reject(BASE+" androidboot.bootreason=warm lastblval=87\n");
        reject(null);
        System.out.println("Boot argument tests passed");
    }
}
