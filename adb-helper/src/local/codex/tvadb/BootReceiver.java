// SPDX-License-Identifier: Apache-2.0
package local.codex.tvadb;
import android.content.*;
import android.util.Log;
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c,Intent i) {
        String a=i.getAction();
        if(Intent.ACTION_BOOT_COMPLETED.equals(a)||Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(a)||Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)||"mitv.action.STR_BOOT_COMPLETED".equals(a)) {
            Log.i(RestoreJob.TAG,"Boot event received: "+a);
            RestoreJob.start(c,a);
        }
    }
}
