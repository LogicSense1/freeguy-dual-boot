// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
import java.util.*;
public final class CecKeyGateTest {
 static final class Target implements CecKeyGate.Target {
  boolean active=true,fail;
  List<String> events=new ArrayList<>();
  public boolean matches(){return active;}
  public void send(int key,boolean down)throws Exception{if(fail)throw new Exception("disconnected");events.add(key+":"+down);}
 }
 static int checks;
 static void check(boolean condition){checks++;if(!condition)throw new AssertionError("check "+checks);}
 public static void main(String[] args){
  Target target=new Target();CecKeyGate gate=new CecKeyGate(target);
  for(int key:new int[]{3,24,25,26,82,164,219,231}){check(!gate.key(key,true));check(!gate.key(key,false));}
  check(target.events.isEmpty());
  check(!gate.key(21,false));check(gate.key(21,true));check(gate.key(21,false));
  check(target.events.equals(Arrays.asList("21:true","21:false")));
  target.active=false;check(!gate.key(22,true));check(target.events.size()==2);
  target.active=true;check(gate.key(22,true));gate.close();
  check(target.events.equals(Arrays.asList("21:true","21:false","22:true","22:false")));
  gate.close();check(target.events.size()==4);check(!gate.key(19,true));
  target=new Target();gate=new CecKeyGate(target);check(gate.key(19,true));target.active=false;gate.close();check(target.events.size()==1);
  target=new Target();gate=new CecKeyGate(target);target.fail=true;check(!gate.key(20,true));target.fail=false;check(!gate.key(20,false));check(target.events.isEmpty());
  for(int key:new int[]{4,19,20,21,22,23,66,85,86,87,88,89,90,126,127})check(CecKeyGate.supported(key));
  check(gate.key(4,true));check(gate.key(4,false));
  check(CecInputLabel.device(3,"PlayStation 5").equals("PlayStation 5"));
  check(CecInputLabel.device(3," ").equals("HDMI 3"));
  check(CecInputLabel.make(3,"PlayStation 5").equals("HDMI 3 · PlayStation 5"));
  check(CecInputLabel.make(3,null).equals("HDMI 3"));
  check(CecInputLabel.make(1," \n\t ").equals("HDMI 1"));
  check(CecInputLabel.make(2,"Box\n\u202e").equals("HDMI 2 · Box"));
  check(CecInputLabel.make(3,new String(new char[100]).replace('\0','A')).length()==57);
  System.out.println("PASS "+checks+" CEC navigation, local-key, ownership and release checks");
 }
}
