// SPDX-License-Identifier: Apache-2.0
import android.bluetooth.*;
import android.content.Context;
import android.os.*;

/** Read one existing remote bond. Output is private input to the root launcher. */
public final class RemoteWakeAddress {
 public static void main(String[] args) throws Exception {
  Thread.setDefaultUncaughtExceptionHandler((t,e)->{e.printStackTrace(System.err);System.exit(1);});
  System.err.println("WAKE_ADDRESS_STAGE main");
  if(args.length!=0)throw new IllegalArgumentException();
  Class<?> props=Class.forName("android.os.SystemProperties");
  if(!"14".equals(props.getMethod("get",String.class).invoke(null,"ro.build.version.release"))||
     !"1".equals(props.getMethod("get",String.class).invoke(null,"ro.codex.fileboot")))throw new SecurityException("Google RAM only");
  android.system.Os.setgid(1000);android.system.Os.setuid(1000);
  Looper.prepareMainLooper();
  System.err.println("WAKE_ADDRESS_STAGE identity");
  Class<?> initializer=Class.forName("android.bluetooth.BluetoothFrameworkInitializer");
  Class<?> manager=Class.forName("android.os.BluetoothServiceManager");
  System.err.println("WAKE_ADDRESS_STAGE classes");
  if(initializer.getMethod("getBluetoothServiceManager").invoke(null)==null)
   initializer.getMethod("setBluetoothServiceManager",manager).invoke(null,manager.getConstructor().newInstance());
  System.err.println("WAKE_ADDRESS_STAGE bluetooth-manager");
  Class<?> at=Class.forName("android.app.ActivityThread");Object thread=at.getMethod("systemMain").invoke(null);
  System.err.println("WAKE_ADDRESS_STAGE system-main");
  Context ctx=(Context)at.getMethod("getSystemContext").invoke(thread);
  System.err.println("WAKE_ADDRESS_STAGE context");
  BluetoothAdapter adapter=((BluetoothManager)ctx.getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter();
  System.err.println("WAKE_ADDRESS_STAGE adapter");
  BluetoothDevice selected=null;
  for(BluetoothDevice d:adapter.getBondedDevices())if("小米蓝牙语音遥控器".equals(d.getName())){
   if(selected!=null)throw new IllegalStateException("Ambiguous remote");selected=d;
  }
  if(selected==null||selected.getBondState()!=BluetoothDevice.BOND_BONDED)
   throw new IllegalStateException("Existing bond required");
  String address=selected.getAddress().toUpperCase(java.util.Locale.ROOT);
  if(!address.matches("([0-9A-F]{2}:){5}[0-9A-F]{2}"))throw new IllegalStateException("Invalid address");
  System.out.println(address);System.out.flush();System.exit(0);
 }
}
