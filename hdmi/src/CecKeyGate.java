// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
import java.util.HashSet;
import java.util.Set;

/** Only a currently selected, visible HDMI session may forward navigation/media keys. */
final class CecKeyGate {
 interface Target { boolean matches(); void send(int key,boolean down)throws Exception; }
 private final Target target;
 private final Set<Integer> held=new HashSet<>();
 private boolean closed;
 CecKeyGate(Target target){this.target=target;}
 static boolean supported(int key){
  // BACK belongs to the selected CEC device. HOME, MENU, POWER, volume and voice stay local.
  switch(key){case 4:case 19:case 20:case 21:case 22:case 23:case 66:
   case 85:case 86:case 87:case 88:case 89:case 90:case 126:case 127:return true;
   default:return false;}
 }
 boolean key(int key,boolean down){
  if(closed||!supported(key))return false;
  if(!target.matches()){held.clear();return false;}
  if(!down&&!held.contains(key))return false;
  try{target.send(key,down);if(down)held.add(key);else held.remove(key);return true;}
  catch(Exception e){held.remove(key);return false;}
 }
 void close(){
  if(closed)return;
  if(target.matches())for(int key:held)try{target.send(key,false);}catch(Exception ignored){}
  held.clear();closed=true;
 }
}
