// SPDX-License-Identifier: Apache-2.0
package local.codex.tvadb;
import android.app.job.*;
import android.content.*;
import android.provider.Settings;
import android.util.Log;
public final class RestoreJob extends JobService {
    static final String PREF="control", TAG="CodexAdbKeeper";
    static final int JOB=1001, PERIODIC=1002;
    static SharedPreferences prefs(Context c) {return c.createDeviceProtectedStorageContext().getSharedPreferences(PREF,0);}
    static boolean enabled(Context c) {return prefs(c).getBoolean("enabled",false);}
    static void restore(Context c,String reason) {
        if (!enabled(c)) return;
        try {
            int before=Settings.Global.getInt(c.getContentResolver(),Settings.Global.ADB_ENABLED,0);
            if(before==0) Settings.Global.putInt(c.getContentResolver(),Settings.Global.ADB_ENABLED,1);
            if(before==0 || !reason.equals("poll")) {
                int after=Settings.Global.getInt(c.getContentResolver(),Settings.Global.ADB_ENABLED,0);
                prefs(c).edit().putLong("last_restore",System.currentTimeMillis()).putInt("last_state",after).putString("last_reason",reason).apply();
                Log.i(TAG,"Restore reason="+reason+", before="+before+", after="+after);
            }
        } catch (RuntimeException e) {Log.e(TAG,"Restore failed",e);}
    }
    static void schedule(Context c) {
        if (!enabled(c)) return;
        JobScheduler js=c.getSystemService(JobScheduler.class);
        ComponentName cn=new ComponentName(c,RestoreJob.class);
        int once=js.schedule(new JobInfo.Builder(JOB,cn).setMinimumLatency(20000).setOverrideDeadline(40000).setPersisted(true).build());
        int periodic=js.schedule(new JobInfo.Builder(PERIODIC,cn).setPeriodic(15*60*1000L).setPersisted(true).build());
        Log.i(TAG,"Persistent jobs scheduled: "+once+","+periodic);
    }
    static void start(Context c,String reason) {
        if(!enabled(c))return;
        restore(c,reason);
        try {c.startForegroundService(new Intent(c,KeeperService.class));}
        catch(RuntimeException e){Log.e(TAG,"Service start failed",e);}
        schedule(c);
    }
    @Override public boolean onStartJob(JobParameters p) {
        if(enabled(this)) {
            restore(this,"job:"+p.getJobId());
            try {startForegroundService(new Intent(this,KeeperService.class));}
            catch(RuntimeException e){Log.e(TAG,"Job service start failed",e);}
        }
        return false;
    }
    @Override public boolean onStopJob(JobParameters p) {return enabled(this);}
}
