// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
public final class SignalRefreshGateTest {
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  SignalRefreshGate g=new SignalRefreshGate();
  check(!g.observe(3840,2160,14400,0));check(!g.observe(3840,2160,14400,1000));
  check(g.observe(3840,2160,14400,2000));g.markApplied();
  for(int n=3;n<20;n++)check(!g.observe(3840,2160,14400+(n%2)*20,n*1000));
  check(!g.observe(3840,2160,6000,20000));check(!g.observe(3840,2160,6000,23000));
  check(!g.observe(3840,2160,14400,24000));check(!g.observe(3840,2160,14400,25000));check(g.observe(3840,2160,14400,26000));g.markApplied();
  check(!g.observe(0,0,0,27000));check(!g.observe(3840,2160,14400,28000));check(!g.observe(3840,2160,14400,29000));check(g.observe(3840,2160,14400,30000));g.markApplied();
  check(!g.observe(3840,2160,12000,31000));check(!g.observe(3840,2160,14400,32000));check(!g.observe(3840,2160,12000,33000));
  check(!g.observe(3840,2160,999999,34000));
  System.out.println("PASS: stable high-rate gate, jitter, no repeated refresh, low/high switch, reconnect, unstable and invalid timing");
 }
}
