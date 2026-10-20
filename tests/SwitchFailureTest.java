// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
public final class SwitchFailureTest {
    public static void main(String[] args){
        String[][] cases={
            {"Unsupported boot reason","BOOT_REASON"},
            {"Code asset integrity mismatch: private-file","FILES"},
            {"Unsupported boot configuration","CONFIG"},
            {"Existing Google data missing","DATA"},
            {"Proxy directory metadata changed","ACCESS"},
            {"TvService unavailable","SERVICE"},
            {"Switch already running","BUSY"},
            {"Google did not reach a stable health lease","HEALTH"},
            {"Default changed concurrently","DEFAULT"},
            {"Original HyperOS B required","SYSTEM"},
            {"Write readback mismatch","IO"}};
        for(String[] c:cases){
            assert SwitchFailure.code(new IllegalStateException(c[0])).equals(c[1]);
            assert SwitchFailure.code(new java.lang.reflect.InvocationTargetException(
                new IllegalStateException(c[0]))).equals(c[1]);
            assert SwitchFailure.safe(c[1]).equals(c[1]);
        }
        assert SwitchFailure.code(new Exception("/private/unexpected-secret")).equals("UNKNOWN");
        assert SwitchFailure.safe("/private/unexpected-secret").equals("UNKNOWN");
        assert SwitchFailure.safe(null).equals("UNKNOWN");
        assert SwitchFailure.code(null).equals("UNKNOWN");
        System.out.println("Switch failure category tests passed");
    }
}
