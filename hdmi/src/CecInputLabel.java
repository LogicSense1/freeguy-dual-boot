// SPDX-License-Identifier: Apache-2.0
package local.codex.hdmi;
final class CecInputLabel {
 static String make(int port,String name){
  String base="HDMI "+port;
  String value=clean(name);
  return value.isEmpty()?base:base+" · "+value;
 }
 static String device(int port,String name){String value=clean(name);return value.isEmpty()?"HDMI "+port:value;}
 private static String clean(String name){
  if(name==null)return "";
  StringBuilder clean=new StringBuilder();
  for(int i=0;i<name.length()&&clean.length()<48;i++){
   char c=name.charAt(i);
   if(!Character.isISOControl(c)&&Character.getType(c)!=Character.FORMAT)clean.append(c);
  }
  return clean.toString().trim();
 }
}
