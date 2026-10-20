// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import java.util.*;
import local.codex.tvswitchprobe.EnvironmentTransaction;
public class EnvironmentTransactionTest {
 static final byte[] M={1,2,3,4},B={5,6,7,8},NM={11,12,13,14},NB={15,16,17,18};
 static void need(boolean b){if(!b)throw new AssertionError();}
 static class IO implements EnvironmentTransaction.IO {
  byte[] m=M.clone(),b=B.clone();int step=0,fail, writes=0;boolean broken=false;
  IO(int f){fail=f;}
  void point()throws Exception{if(++step==fail||broken)throw new Exception("injected");}
  public byte[] read(boolean main)throws Exception{point();return (main?m:b).clone();}
  public void write(boolean main,byte[] data)throws Exception{writes++;byte[] target=main?m:b;System.arraycopy(data,0,target,0,2);point();System.arraycopy(data,0,target,0,4);}
  public void sync()throws Exception{point();}
  void original(){need(Arrays.equals(m,M)&&Arrays.equals(b,B));}
 }
 public static void main(String[] args)throws Exception{
  int cases=0;
  IO ok=new IO(0);EnvironmentTransaction good=new EnvironmentTransaction(ok,M,B);good.commit(NM,NB);
  need(Arrays.equals(ok.m,NM)&&Arrays.equals(ok.b,NB));int points=ok.step;cases++;
  good.rollback();ok.original();need(!good.attempted());cases++;
  for(int n=1;n<=points;n++){
   IO io=new IO(n);EnvironmentTransaction t=new EnvironmentTransaction(io,M,B);boolean failed=false;
   try{t.commit(NM,NB);}catch(Exception e){failed=true;}
   need(failed);if(t.attempted())t.rollback();io.original();cases++;
  }
  IO changed=new IO(0);changed.m[0]=99;EnvironmentTransaction invalid=new EnvironmentTransaction(changed,M,B);
  try{invalid.commit(NM,NB);throw new AssertionError();}catch(IllegalStateException expected){}
  need(changed.writes==0&&!invalid.attempted()&&changed.m[0]==99);cases++;
  IO broken=new IO(0);EnvironmentTransaction uncertain=new EnvironmentTransaction(broken,M,B);uncertain.commit(NM,NB);broken.broken=true;
  try{uncertain.rollback();throw new AssertionError();}catch(Exception expected){}
  need(uncertain.attempted());cases++;
  System.out.println("{\"passed\":true,\"cases\":"+cases+",\"commitIoFaultPoints\":"+points+",\"deviceAccess\":false,\"realPowerLossProven\":false}");
 }
}
