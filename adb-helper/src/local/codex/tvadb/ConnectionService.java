// SPDX-License-Identifier: Apache-2.0
package local.codex.tvadb;
import android.accessibilityservice.AccessibilityService;
import android.view.accessibility.AccessibilityEvent;
import android.database.ContentObserver;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;

/** System-bound lifecycle only. No UI events, window access, or UI actions. */
public final class ConnectionService extends AccessibilityService {
    private final Handler handler=new Handler(Looper.getMainLooper());
    private boolean registered=false;
    private final ContentObserver observer=new ContentObserver(handler) {
        @Override public void onChange(boolean selfChange) {
            handler.postDelayed(()->RestoreJob.restore(ConnectionService.this,"bound-setting-changed"),1500);
        }
    };
    private final Runnable poll=new Runnable(){public void run(){
        if(!RestoreJob.enabled(ConnectionService.this)){disableSelf();return;}
        RestoreJob.restore(ConnectionService.this,"poll");handler.postDelayed(this,10000);
    }};
    @Override protected void onServiceConnected(){
        Log.i(RestoreJob.TAG,"System-bound connection service connected; UI capabilities disabled");
        if(!registered){
            getContentResolver().registerContentObserver(Settings.Global.getUriFor(Settings.Global.ADB_ENABLED),false,observer);
            registered=true;
        }
        RestoreJob.restore(this,"system-bound-start");
        handler.removeCallbacks(poll);handler.post(poll);
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}
    @Override public void onInterrupt() {}
    @Override public void onDestroy(){
        if(registered)getContentResolver().unregisterContentObserver(observer);
        handler.removeCallbacksAndMessages(null);super.onDestroy();
    }
}
