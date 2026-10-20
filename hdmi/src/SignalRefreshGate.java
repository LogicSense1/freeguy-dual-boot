// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;

/** One refresh per stable input mode, tolerating small measured-rate jitter. */
final class SignalRefreshGate {
 int width,height,fps,samples; boolean applied;
 long since;
 boolean observe(int w,int h,int f,long now) {
  if(w<1||h<1||w>8192||h>4320||f<2300||f>24000){reset();return false;}
  if(w!=width||h!=height||Math.abs(f-fps)>200){
   width=w;height=h;fps=f;samples=1;since=now;applied=false;return false;
  }
  samples++;
  return !applied&&fps>6200&&samples>=3&&now-since>=2000;
 }
 void markApplied(){applied=true;}
 void reset(){width=height=fps=samples=0;applied=false;since=0;}
}
