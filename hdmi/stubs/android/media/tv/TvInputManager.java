// SPDX-License-Identifier: Apache-2.0
package android.media.tv;
/** Compile only. Verified against live Android 14 reflection; never put in dex. */
public class TvInputManager {
 public static abstract class TvInputCallback {
  public void onInputStateChanged(String id,int state){}
 }
 public static abstract class HardwareCallback {
  public abstract void onReleased();
  public abstract void onStreamConfigChanged(TvStreamConfig[] configs);
 }
}
