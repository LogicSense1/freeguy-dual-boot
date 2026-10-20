// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.zip.CRC32;

/** Synthetic environments only; no firmware or device identifiers. */
public final class BootEnvironmentTest {
    static int checks;
    static final String STOCK = "console=ttyS0 root=/dev/ram init=/init rw loglevel=0 test=synthetic";
    interface Action { void run(); }
    static void check(boolean value) {
        if (!value) throw new AssertionError("check " + checks);
        checks++;
    }
    static void reject(Action action) {
        try { action.run(); }
        catch (IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("expected rejection at " + checks);
    }
    static void checksum(byte[] image) {
        CRC32 crc = new CRC32();
        crc.update(image, 4, image.length - 4);
        long value = crc.getValue();
        for (int i = 0; i < 4; i++) image[i] = (byte) (value >>> (8 * i));
    }
    static byte[] image(String args, String extra) {
        byte[] out = new byte[65536];
        byte[] body = ("bootargs=" + args + "\0binary=\u00ff\0" + extra + "\0\0")
                .getBytes(StandardCharsets.ISO_8859_1);
        System.arraycopy(body, 0, out, 4, body.length);
        checksum(out);
        return out;
    }
    public static void main(String[] args) {
        byte[] original = image(STOCK, "");
        check(BootEnvironment.bootArguments(original).equals(STOCK));
        for (String loader : new String[]{"envdaily", "envwifi"}) {
            byte[] trial = BootEnvironment.transform(original, true, loader);
            check(BootEnvironment.bootArguments(trial).contains("init=/local/tmp/rd-root/" + loader));
            check(Arrays.equals(original, BootEnvironment.transform(trial, false, loader)));
            reject(() -> BootEnvironment.transform(trial, true, loader));
            reject(() -> BootEnvironment.transform(original, false, loader));
        }
        check(Arrays.equals(original, image(STOCK, ""))); // input was not mutated
        reject(() -> BootEnvironment.transform(original, true, "../other"));
        reject(() -> BootEnvironment.bootArguments(new byte[10]));
        byte[] badCrc = original.clone(); badCrc[0] ^= 1;
        reject(() -> BootEnvironment.bootArguments(badCrc));
        reject(() -> BootEnvironment.transform(image(STOCK, "bootargs=" + STOCK + "\0"), true));
        for (String bad : new String[]{
                STOCK + " root=/dev/ram", STOCK + " root=/other",
                STOCK + " loglevel=0", STOCK + " ro", STOCK.replace(" rw ", " "),
                STOCK + " rdinit=/other", STOCK + " rootflags=other",
                STOCK + " rootfstype=other", STOCK + " ", " " + STOCK,
                STOCK.replace(" rw ", "  rw "), STOCK + "\ttab"}) {
            reject(() -> BootEnvironment.transform(image(bad, ""), true));
        }
        byte[] padding = original.clone(); padding[60000] = 1; checksum(padding);
        reject(() -> BootEnvironment.bootArguments(padding));
        String nearLimit = STOCK + " padding=" + "a".repeat(2047 - STOCK.length() - 9);
        check(nearLimit.length() == 2047);
        reject(() -> BootEnvironment.transform(image(nearLimit, ""), true));
        System.out.println("BootEnvironmentTest: " + checks + " checks passed");
    }
}
