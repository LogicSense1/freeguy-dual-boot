// SPDX-License-Identifier: Apache-2.0
/** Bounded existing-bond HID connection. Never creates/removes a bond or changes policy. */
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.Context;
import android.os.*;
import java.util.*;

public final class RemoteReconnectProbe {
  static BluetoothAdapter adapter;
  static BluetoothProfile profile;
  static BluetoothDevice remote;
  static BluetoothLeScanner scanner;
  static ScanCallback callback;
  static Handler handler;
  static boolean attempted;
  static boolean finished;
  static long readySince;
  static void finishObservation() {
    if(finished)return;finished=true;
    if(scanner!=null&&callback!=null)scanner.stopScan(callback);
    if(profile!=null){System.out.println("hidAfter="+profile.getConnectionState(remote));adapter.closeProfileProxy(4,profile);}
    System.out.println("bondStateAfter="+remote.getBondState());System.exit(0);
  }
  static void connect() {
    if (profile == null || attempted || remote.getBondState()!=BluetoothDevice.BOND_BONDED) return;
    attempted = true;
    try {
      System.out.println("hidBefore="+profile.getConnectionState(remote));
      System.out.println("connectAccepted="+profile.getClass().getMethod("connect",BluetoothDevice.class).invoke(profile,remote));
    } catch (Throwable e) { System.out.println("connectError="+e); }
  }
  public static void main(String[] args) throws Exception {
    if(args.length!=1 || !"ready-exit".equals(args[0]))throw new IllegalArgumentException("Existing-bond readiness only");
    Class<?> props=Class.forName("android.os.SystemProperties");
    if(!"14".equals(props.getMethod("get",String.class).invoke(null,"ro.build.version.release")) || !"1".equals(props.getMethod("get",String.class).invoke(null,"ro.codex.fileboot")))throw new SecurityException("Google runtime required");
    // Drop root to match the system Context's attribution identity.
    android.system.Os.setgid(1000);android.system.Os.setuid(1000);
    Thread.setDefaultUncaughtExceptionHandler((t,e)->{e.printStackTrace(System.err);System.exit(1);});
    Looper.prepareMainLooper(); handler = new Handler(Looper.getMainLooper());
    Class<?> initializer=Class.forName("android.bluetooth.BluetoothFrameworkInitializer");
    Class<?> manager=Class.forName("android.os.BluetoothServiceManager");
    if(initializer.getMethod("getBluetoothServiceManager").invoke(null)==null)
      initializer.getMethod("setBluetoothServiceManager",manager).invoke(null,manager.getConstructor().newInstance());
    Class<?> at=Class.forName("android.app.ActivityThread");
    Object thread=at.getMethod("systemMain").invoke(null);
    Context context=(Context)at.getMethod("getSystemContext").invoke(thread);
    adapter=((BluetoothManager)context.getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter();
    for (BluetoothDevice d:adapter.getBondedDevices()) {
      if ("小米蓝牙语音遥控器".equals(d.getName())) {
        if(remote!=null) throw new IllegalStateException("Ambiguous remote"); remote=d;
      }
    }
    if(remote==null) throw new IllegalStateException("Expected existing remote bond absent");
    System.out.println("existingBond="+(remote.getBondState()==BluetoothDevice.BOND_BONDED));
    adapter.getProfileProxy(context,new BluetoothProfile.ServiceListener(){
      public void onServiceConnected(int id,BluetoothProfile p){profile=p;System.out.println("hidProxy=true");connect();}
      public void onServiceDisconnected(int id){profile=null;}
    },4);
    scanner=adapter.getBluetoothLeScanner();
    callback=new ScanCallback(){
      public void onScanResult(int type,ScanResult result){System.out.println("remoteAdvertisement=true existingAddress="+remote.equals(result.getDevice())+" rssi="+result.getRssi());connect();}
      public void onScanFailed(int error){System.out.println("scanFailed="+error);}
    };
    List<ScanFilter> filters=new ArrayList<>();
    filters.add(new ScanFilter.Builder().setDeviceAddress(remote.getAddress()).build());
    filters.add(new ScanFilter.Builder().setDeviceName("小米蓝牙语音遥控器").build());
    filters.add(new ScanFilter.Builder().setDeviceName("Xiaomi RC").build());
    scanner.startScan(filters,new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),callback);
    handler.postDelayed(new Runnable(){public void run(){
      if(finished)return;
      boolean connected=profile!=null&&profile.getConnectionState(remote)==BluetoothProfile.STATE_CONNECTED&&remote.getBondState()==BluetoothDevice.BOND_BONDED;
      long now=SystemClock.uptimeMillis();
      if(!connected)readySince=0;
      else if(readySince==0)readySince=now;
      else if(now-readySince>=1000){System.out.println("existingHidReadyEarly=true");finishObservation();return;}
      handler.postDelayed(this,250);
    }},250);
    handler.postDelayed(()->finishObservation(),45000);
    System.out.println("listeningSeconds="+45); Looper.loop();
  }
}
