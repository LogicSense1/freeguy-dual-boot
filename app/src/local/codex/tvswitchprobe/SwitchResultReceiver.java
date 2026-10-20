// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import android.content.*;
public final class SwitchResultReceiver extends BroadcastReceiver {
    public void onReceive(Context c,Intent i){
        if(!"local.codex.tvswitchprobe.RESULT".equals(i.getAction()))return;
        String s=i.getStringExtra("state");
        if(s==null||!s.matches("CHECKING|READY|RESTARTING|FAILED|RESTORED|RECOVERY_REQUIRED"))return;
        SwitchActivity a=SwitchActivity.visible;
        if(a!=null)a.update(s,Math.max(0,i.getIntExtra("current",0)),Math.max(0,i.getIntExtra("total",0)));
    }
}
