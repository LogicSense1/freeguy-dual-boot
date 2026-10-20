// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import android.app.job.*;
import android.content.*;
import android.os.SystemClock;
/** Finite readiness wait. No periodic job and no retries of a failed transition. */
public final class DefaultBootJob extends JobService {
    private volatile boolean stopped;
    static synchronized void schedule(Context c){
        if(!AssetPins.ENABLE_PERSISTENT_DEFAULT)return;
        int boot=android.provider.Settings.Global.getInt(c.getContentResolver(),"boot_count",-1);
        if(boot<0){android.util.Log.e("CodexSystemSwitch","Boot count unavailable; automatic entry skipped");return;}
        android.content.SharedPreferences seen=c.getSharedPreferences("default-boot-dispatch-v1",Context.MODE_PRIVATE);
        if(seen.getInt("boot",-1)==boot)return;
        // Record before scheduling so a rebind or STR wake cannot repeat a failed attempt.
        if(!seen.edit().putInt("boot",boot).commit())return;
        int result=c.getSystemService(JobScheduler.class).schedule(new JobInfo.Builder(2001,new ComponentName(c,DefaultBootJob.class)).setMinimumLatency(5000).setOverrideDeadline(10000).build());
        android.util.Log.i("CodexSystemSwitch","Boot job scheduled: boot="+boot+" result="+result);
    }
    @Override public boolean onStartJob(JobParameters p){
        if(!AssetPins.ENABLE_PERSISTENT_DEFAULT)return false;
        stopped=false;
        new Thread(()->{
            try {
                long end=SystemClock.uptimeMillis()+180000;
                while(!stopped&&SystemClock.uptimeMillis()<end){
                    Object b=Class.forName("android.os.ServiceManager").getMethod("getService",String.class).invoke(null,"TvService");
                    if(b!=null&&"1".equals(Class.forName("android.os.SystemProperties").getMethod("get",String.class).invoke(null,"sys.boot_completed"))){
                        boolean google=SwitchBridge.isGoogle();
                        // Keep the health wait in this scheduled app job. OEM
                        // service restarts kill processes launched in its cgroup.
                        if(google)SwitchWorker.awaitDailyHealth(()->stopped);
                        if(!stopped)SwitchBridge.start(this,google?"google-healthy":"auto-google");
                        break;
                    }
                    Thread.sleep(3000);
                }
            }catch(Exception e){android.util.Log.e("CodexSystemSwitch","Boot readiness stopped",e);}
            finally{jobFinished(p,false);}
        },"default-boot-readiness").start();
        return true;
    }
    @Override public boolean onStopJob(JobParameters p){stopped=true;return false;}
}
