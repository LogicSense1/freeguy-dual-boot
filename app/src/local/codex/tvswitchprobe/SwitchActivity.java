// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import android.app.*;
import android.os.Bundle;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.*;
import android.widget.*;

public final class SwitchActivity extends Activity {
    static SwitchActivity visible;
    private TextView status;private Button enter;private ProgressBar progress;
    private boolean busy=false,ready=false,google=false;
    private final int WHITE=0xfff2f5fc,MUTED=0xffaebbd0,GREEN=0xff94e3c4;
    private int dp(int x){return (int)(x*getResources().getDisplayMetrics().density+.5f);}
    private GradientDrawable bg(int color,int stroke){GradientDrawable g=new GradientDrawable();g.setColor(color);g.setCornerRadius(dp(18));if(stroke!=0)g.setStroke(dp(2),stroke);return g;}
    private String string(String name,Object... args){return getString(getResources().getIdentifier(name,"string",getPackageName()),args);}
    private TextView text(String s,int size,int color){TextView t=new TextView(this);t.setText(s);t.setTextSize(size);t.setTextColor(color);return t;}
    private void gap(LinearLayout p,int h){p.addView(new View(this),new LinearLayout.LayoutParams(1,dp(h)));}
    private LinearLayout card(String tag,String name,int accent){
        LinearLayout c=new LinearLayout(this);c.setOrientation(1);c.setGravity(Gravity.CENTER_VERTICAL);c.setPadding(dp(28),dp(24),dp(28),dp(24));c.setBackground(bg(0xff172238,0));
        c.addView(text(tag,14,accent));gap(c,16);TextView t=text(name,32,WHITE);t.setTypeface(null,Typeface.BOLD);c.addView(t);return c;
    }
    private Button button(String label){Button b=new Button(this);b.setText(label);b.setTextSize(16);b.setAllCaps(false);b.setTextColor(WHITE);b.setBackground(bg(0xff273950,0));b.setOnFocusChangeListener((v,f)->v.setBackground(bg(f?0xff355a5b:0xff273950,f?GREEN:0)));return b;}
    @Override public void onCreate(Bundle b){super.onCreate(b);visible=this;google=SwitchBridge.isGoogle();getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON|WindowManager.LayoutParams.FLAG_FULLSCREEN);
        LinearLayout root=new LinearLayout(this);root.setOrientation(1);root.setGravity(Gravity.CENTER_VERTICAL);root.setPadding(dp(68),dp(32),dp(68),dp(32));root.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,new int[]{0xff101b2b,0xff0b1220}));
        LinearLayout heading=new LinearLayout(this);heading.setGravity(Gravity.CENTER_VERTICAL);ImageView icon=new ImageView(this);icon.setImageResource(getResources().getIdentifier("ic_switch","drawable",getPackageName()));heading.addView(icon,new LinearLayout.LayoutParams(dp(46),dp(46)));
        TextView title=text(string("title"),28,WHITE);title.setTypeface(null,Typeface.BOLD);LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(-2,-2);tp.leftMargin=dp(16);heading.addView(title,tp);root.addView(heading);gap(root,30);
        LinearLayout cards=new LinearLayout(this);LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(0,dp(150),1);cards.addView(card(string("current"),string(google?"google_tv":"hyperos"),google?GREEN:0xffffbc83),cp);
        TextView arrow=text("→",30,MUTED);arrow.setGravity(Gravity.CENTER);cards.addView(arrow,new LinearLayout.LayoutParams(dp(64),dp(150)));cards.addView(card(string("switch_to"),string(google?"hyperos":"google_tv"),google?0xffffbc83:GREEN),new LinearLayout.LayoutParams(0,dp(150),1));root.addView(cards);gap(root,20);
        root.addView(text(AssetPins.ENABLE_PERSISTENT_DEFAULT?string("default_after",string(google?"hyperos":"google_tv")):string("next_start",string("hyperos")),14,MUTED));gap(root,24);
        LinearLayout actions=new LinearLayout(this);enter=button(string("restart_to",string(google?"hyperos":"google_tv")));enter.setEnabled(false);enter.setAlpha(.45f);actions.addView(enter,new LinearLayout.LayoutParams(dp(280),dp(54)));
        Button cancel=button(string("cancel"));LinearLayout.LayoutParams cancelParams=new LinearLayout.LayoutParams(dp(120),dp(54));cancelParams.leftMargin=dp(16);actions.addView(cancel,cancelParams);cancel.setOnClickListener(v->finish());root.addView(actions);gap(root,14);
        progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);progress.setMax(14);progress.setProgressTintList(android.content.res.ColorStateList.valueOf(GREEN));root.addView(progress,new LinearLayout.LayoutParams(dp(280),dp(3)));gap(root,8);
        status=text(string("preparing"),13,MUTED);root.addView(status);
        setContentView(root);cancel.requestFocus();enter.setOnClickListener(v->{if(ready&&!busy){if(google){if(AssetPins.ENABLE_PERSISTENT_DEFAULT){start("return-stock");return;}busy=true;enter.setEnabled(false);status.setText(string("restarting"));new Thread(()->{try{SwitchBridge.returnToStock();}catch(Exception e){android.util.Log.e("CodexSystemSwitch","Return failed",e);runOnUiThread(()->{busy=false;enter.setEnabled(true);status.setText(string("switch_failed"));});}},"return-stock").start();}else start("enter-google");}});
        if(google){ready=true;enter.setEnabled(true);enter.setAlpha(1f);progress.setVisibility(View.INVISIBLE);status.setText("");enter.requestFocus();}else start("check");
    }
    private void start(String action){if(busy)return;busy=true;ready=false;enter.setEnabled(false);enter.setAlpha(.45f);progress.setVisibility(View.VISIBLE);progress.setIndeterminate(true);status.setText(action.equals("check")?string("preparing"):string("switching"));new Thread(()->{try{SwitchBridge.start(this,action);}catch(Exception e){android.util.Log.e("CodexSystemSwitch","Bridge failed",e);runOnUiThread(()->update("FAILED",0,0));}},"switch-launch").start();}
    public void update(String state,int n,int total){runOnUiThread(()->{
        if(state.equals("CHECKING")){progress.setIndeterminate(false);progress.setMax(Math.max(1,total));progress.setProgress(n);return;}
        progress.setIndeterminate(false);busy=false;ready=state.equals("READY");enter.setEnabled(ready&&AssetPins.ENABLE_ENTER_GOOGLE);enter.setAlpha(enter.isEnabled()?1f:.45f);
        if(ready){progress.setVisibility(View.INVISIBLE);status.setText(AssetPins.ENABLE_ENTER_GOOGLE?"":string("unavailable"));if(enter.isEnabled())enter.requestFocus();}
        else if(state.equals("RESTARTING")){busy=true;status.setText(string("restarting"));}
        else if(state.equals("RESTORED")){status.setText(string("restored"));}
        else if(state.equals("RECOVERY_REQUIRED")){status.setText(string("recovery_required"));}
        else{status.setText(string("switch_failed"));}
    });}
    @Override protected void onDestroy(){if(visible==this)visible=null;super.onDestroy();}
}
