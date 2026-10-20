// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;
import java.util.*;

/** Enumerate interruption points before and after each rename, including fsync failure. */
public final class ImageTransactionTest {
 static final List<ImageTransaction.Row> ROWS=new ArrayList<>();
 static {for(int i=0;i<4;i++)ROWS.add(new ImageTransaction.Row("active"+i,"next"+i,"old"+i,"A"+i,"B"+i));}
 static final class Stop extends Exception {}
 static final class Disk implements ImageTransaction.IO {
  final Map<String,String> files=new HashMap<>();int moves,fail=-1;boolean after,guardFails;
  Disk(){for(ImageTransaction.Row r:ROWS){files.put(r.active,r.oldHash);files.put(r.candidate,r.newHash);}}
  public String hash(String p){return files.get(p);}
  public void guard()throws Exception {if(guardFails)throw new Stop();}
  public void move(String a,String b)throws Exception {
   if(++moves==fail&&!after)throw new Stop();
   if(files.containsKey(b)||!files.containsKey(a))throw new IllegalStateException();
   files.put(b,files.remove(a));
   if(moves==fail&&after)throw new Stop();
  }
 }
 static int tests;
 static void need(boolean b){if(!b)throw new AssertionError();}
 static ImageTransaction tx(Disk d){return new ImageTransaction(d,ROWS);}
 static void original(Disk d)throws Exception {need(tx(d).inspect().stream().allMatch(s->s==ImageTransaction.State.ORIGINAL));need(d.files.size()==8);}
 public static void main(String[] args)throws Exception {
  Disk plain=new Disk();tx(plain).apply();need(plain.files.size()==8);tx(plain).rollback();original(plain);tests++;
  for(boolean after:new boolean[]{false,true})for(int point=1;point<=8;point++) {
   for(boolean resume:new boolean[]{false,true}) {
    Disk d=new Disk();d.fail=point;d.after=after;
    try{tx(d).apply();throw new AssertionError();}catch(Stop expected){}
    d.fail=-1;
    if(resume)tx(d).apply();
    tx(d).rollback();original(d);tests++;
   }
   Disk d=new Disk();tx(d).apply();d.moves=0;d.fail=point;d.after=after;
   try{tx(d).rollback();throw new AssertionError();}catch(Stop expected){}
   d.fail=-1;tx(d).rollback();original(d);tests++;
  }
  for(String unknown:new String[]{"foreign","SYMLINK","DIRECTORY","TRUNCATED"}) {
   Disk d=new Disk();d.files.put("next3",unknown);Map<String,String> before=new HashMap<>(d.files);
   try{tx(d).apply();throw new AssertionError();}catch(IllegalStateException expected){}
   need(before.equals(d.files)&&d.moves==0);tests++;
  }
  Disk d=new Disk();d.files.put("old3","occupied");
  try{tx(d).apply();throw new AssertionError();}catch(IllegalStateException expected){}
  need(d.moves==0);tests++;
  Disk g=new Disk();g.guardFails=true;
  try{tx(g).apply();throw new AssertionError();}catch(Stop expected){}
  need(g.moves==0);tests++;
  try{new ImageTransaction(g,Arrays.asList(ROWS.get(0),ROWS.get(0)));throw new AssertionError();}catch(IllegalStateException expected){}tests++;
  System.out.println("PASS "+tests+" image transaction cases");
 }
}
