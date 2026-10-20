// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import android.content.*;
/** Only full boots, never STR wakeups or package updates, can schedule a switch. */
public final class DefaultBootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c,Intent i){
        if(AssetPins.ENABLE_PERSISTENT_DEFAULT&&Intent.ACTION_BOOT_COMPLETED.equals(i.getAction()))DefaultBootJob.schedule(c);
    }
}
