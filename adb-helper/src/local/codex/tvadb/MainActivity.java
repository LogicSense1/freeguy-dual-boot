// SPDX-License-Identifier: Apache-2.0
package local.codex.tvadb;
import android.app.Activity;
import android.app.job.JobScheduler;
import android.os.Bundle;
import android.widget.*;
public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        // Launching this dedicated helper opts into restoration; disabling is explicit below.
        RestoreJob.prefs(this).edit().putBoolean("enabled",true).apply();
        RestoreJob.start(this,"activity");
        LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);l.setPadding(60,45,60,45);
        TextView t=new TextView(this);t.setTextSize(23);t.setText(R.string.recovery_enabled);l.addView(t);
        Button off=new Button(this);off.setText(R.string.turn_off);off.setOnClickListener(v->{RestoreJob.prefs(this).edit().putBoolean("enabled",false).apply();((JobScheduler)getSystemService(JOB_SCHEDULER_SERVICE)).cancelAll();stopService(new android.content.Intent(this,KeeperService.class));finish();});l.addView(off);
        Button exit=new Button(this);exit.setText(R.string.done);exit.setOnClickListener(v->finish());l.addView(exit);exit.requestFocus();setContentView(l);
    }
}
