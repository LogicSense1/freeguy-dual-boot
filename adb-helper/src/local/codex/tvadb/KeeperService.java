// SPDX-License-Identifier: Apache-2.0
package local.codex.tvadb;
import android.app.*;
import android.content.*;
import android.database.ContentObserver;
import android.os.*;
import android.provider.Settings;
public final class KeeperService extends Service {
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ContentObserver observer=new ContentObserver(handler) {
        @Override public void onChange(boolean selfChange) {handler.postDelayed(()->RestoreJob.restore(KeeperService.this,"setting-changed"),1500);}
    };
    private final Runnable poll=new Runnable(){public void run(){
        if(!RestoreJob.enabled(KeeperService.this)){stopSelf();return;}
        RestoreJob.restore(KeeperService.this,"poll");handler.postDelayed(this,10000);
    }};
    @Override public void onCreate(){
        super.onCreate();
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("connection",getString(R.string.app_name),NotificationManager.IMPORTANCE_LOW));
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        startForeground(1,new Notification.Builder(this,"connection").setSmallIcon(android.R.drawable.stat_notify_sync).setContentTitle(getString(R.string.recovery_enabled)).setContentText(getString(R.string.notification_action)).setContentIntent(open).setOngoing(true).build());
        getContentResolver().registerContentObserver(Settings.Global.getUriFor(Settings.Global.ADB_ENABLED),false,observer);
        handler.post(poll);
    }
    @Override public int onStartCommand(Intent i,int flags,int id){RestoreJob.restore(this,"service-start");return START_STICKY;}
    @Override public void onDestroy(){getContentResolver().unregisterContentObserver(observer);handler.removeCallbacksAndMessages(null);super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
