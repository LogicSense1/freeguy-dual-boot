// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
import android.app.Activity;
import android.content.*;
import android.os.*;
import android.widget.TextView;
/** Explicit maintenance entry for reconnecting this app after an in-place update. */
public final class ServiceRefreshActivity extends Activity {
 boolean bound;final Handler handler=new Handler(Looper.getMainLooper());
 final ServiceConnection connection=new ServiceConnection(){
  public void onServiceConnected(ComponentName n,IBinder binder){handler.postDelayed(()->finish(),1500);}
  public void onServiceDisconnected(ComponentName n){}
 };
 @Override public void onCreate(Bundle state){
  super.onCreate(state);TextView text=new TextView(this);text.setText("Connecting HDMI input service…");text.setTextSize(26);setContentView(text);
  bound=bindService(new Intent(this,HardwareCecService.class).setAction("android.media.tv.TvInputService"),connection,Context.BIND_AUTO_CREATE);
  handler.postDelayed(()->finish(),8000);
 }
 @Override public void onDestroy(){handler.removeCallbacksAndMessages(null);if(bound)unbindService(connection);super.onDestroy();}
}
