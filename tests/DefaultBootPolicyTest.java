// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.util.*;
import local.codex.tvswitchprobe.*;
public class DefaultBootPolicyTest {
 static int cases=0;
 static String a(int n){return String.format("00000000-0000-0000-0000-%012d",n);}
 static void check(boolean b){if(!b)throw new AssertionError("case "+cases);cases++;}
 interface Action {void run()throws Exception;}
 static void rejects(Action r)throws Exception{try{r.run();}catch(Exception expected){cases++;return;}throw new AssertionError("Expected rejection");}
 static class IO implements DefaultStateTransaction.IO {
  byte[] bytes;int count,fail,envWrites,reboots;
  IO(byte[] b,int f){bytes=b;fail=f;}
  void point()throws Exception{if(++count==fail)throw new Exception("Injected I/O failure");}
  public byte[] read()throws Exception{point();return bytes==null?null:bytes.clone();}
  public void replace(byte[] b)throws Exception{point();byte[] tmp=b.clone();point();/* file fsync */point();bytes=tmp;point();/* directory fsync */}
 }
 public static void main(String[] args)throws Exception {
  DefaultBootPolicy x=DefaultBootPolicy.xiaomi();
  check(DefaultBootPolicy.decode(x.encode()).target==DefaultBootPolicy.Target.XIAOMI);
  check(!x.stockBoot(a(1),a(10),false).enterGoogle);
  DefaultBootPolicy p=x.selectGoogle(a(1),a(10));
  check(p.phase==DefaultBootPolicy.Phase.PENDING);
  check(!p.stockBoot(a(1),a(11),false).enterGoogle); // duplicate boot broadcast
  check(p.stockBoot(a(2),a(11),false).state.phase==DefaultBootPolicy.Phase.PAUSED);
  check(!p.stockBoot(a(2),a(11),false).state.stockBoot(a(3),a(12),false).enterGoogle);
  rejects(()->p.googleHealthy(a(1),a(10))); // cannot acknowledge in stock boot
  rejects(()->p.googleHealthy(a(2),a(11))); // stale/unrelated attempt
  DefaultBootPolicy h=p.googleHealthy(a(2),a(10));
  check(h.googleHealthy(a(2),a(10))==h); // duplicate healthy signal
  rejects(()->h.googleHealthy(a(3),a(10)));
  DefaultBootPolicy.Decision d=h.stockBoot(a(3),a(11),false);
  check(d.enterGoogle&&d.state.phase==DefaultBootPolicy.Phase.PENDING);
  check(!d.state.stockBoot(a(3),a(12),false).enterGoogle);
  check(d.state.stockBoot(a(4),a(12),false).state.phase==DefaultBootPolicy.Phase.PAUSED);
  check(!h.stockBoot(a(1),a(12),false).enterGoogle);
  check(!h.stockBoot(a(3),a(11),true).enterGoogle);
  check(h.stockBoot(a(3),a(11),true).state.phase==DefaultBootPolicy.Phase.PAUSED);
  rejects(()->h.stockBoot(a(2),a(11),false));
  rejects(()->h.selectGoogle("bad-boot-id",a(11)));
  rejects(()->h.selectGoogle(a(3),a(10)));
  DefaultBootPolicy paused=h.pause();
  check(!paused.stockBoot(a(3),a(11),false).enterGoogle);
  check(paused.selectGoogle(a(3),a(11)).phase==DefaultBootPolicy.Phase.PENDING); // explicit user retry
  for(DefaultBootPolicy s:new DefaultBootPolicy[]{x,p,h,paused}){
   check(Arrays.equals(DefaultBootPolicy.decode(s.encode()).encode(),s.encode()));
   byte[] bytes=s.encode();
   for(int i=0;i<bytes.length;i++){byte[] changed=bytes.clone();changed[i]^=1;rejects(()->DefaultBootPolicy.decode(changed));}
   for(int i=0;i<bytes.length;i++){byte[] shortRead=Arrays.copyOf(bytes,i);rejects(()->DefaultBootPolicy.decode(shortRead));}
  }
  IO good=new IO(null,0);DefaultStateTransaction t=new DefaultStateTransaction(good);
  t.commit(x,p);int points=good.count;check(Arrays.equals(good.bytes,p.encode()));
  for(int f=1;f<=points;f++){
   IO io=new IO(null,f);DefaultStateTransaction tx=new DefaultStateTransaction(io);
   try{tx.commit(x,p);io.envWrites++;io.reboots++;}catch(Exception expected){}
   check(io.envWrites==0&&io.reboots==0);
   DefaultBootPolicy after=new DefaultStateTransaction(new IO(io.bytes,0)).read();
   check(!after.stockBoot(a(3),a(12),false).enterGoogle);
  }
  IO concurrent=new IO(h.encode(),0);
  rejects(()->new DefaultStateTransaction(concurrent).commit(x,p));
  IO corrupt=new IO(new byte[128],0);rejects(()->new DefaultStateTransaction(corrupt).read());
  IO back=new IO(h.encode(),0);new DefaultStateTransaction(back).commit(h,x);
  check(!DefaultBootPolicy.decode(back.bytes).stockBoot(a(9),a(99),false).enterGoogle);
  System.out.println("{\"passed\":true,\"assertions\":"+cases+",\"preferenceIoFaultPoints\":"+points+",\"deviceAccess\":false,\"realPowerLossProven\":false}");
 }
}
