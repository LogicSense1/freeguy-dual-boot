// SPDX-License-Identifier: Apache-2.0
/* Candidate only. Remove this exact trial from both factory env files.
 * The backup is flushed and verified before any main-file write.
 * Never creates/truncates an environment file, changes another key, or
 * writes a raw block device. Any I/O failure reboots for UART recovery.
 */
#include "internal-env-inspect.h"
#include "internal-env-transform.c"
static B main_before[65536],bak_before[65536],main_after[65536],bak_after[65536];
static U word(const B*p){return (U)p[0]|((U)p[1]<<8)|((U)p[2]<<16)|((U)p[3]<<24);}
static void read_exact_env(long f,B*p){
 need(sc(19,f,0,0,0));U n=0;
 while(n<65536){long k=sc(3,f,(long)(p+n),65536-n,0);if(k<=0 || (U)k>65536-n)fail();n+=k;}
 B extra;if(sc(3,f,(long)&extra,1,0)!=0)fail();
}
static long open_existing_env(const char*p){
 long f=sc(5,(long)p,2|131072,0,0);need(f);
 B st[128];need(sc(197,f,(long)st,0,0));
 if((word(st+16)&0170000)!=0100000 || word(st+20)!=1 || word(st+48)!=65536 || word(st+52))fail();
 return f;
}
static void commit_existing_env(long f,const B*p){
 need(sc(19,f,0,0,0));writeall(f,p,65536);need(sc(118,f,0,0,0));
 read_exact_env(f,envbuffer);
 for(U i=0;i<65536;i++)if(envbuffer[i]!=p[i])fail();
}
static void restore_trial_env(void){
 need(sc(39,(long)".codex-sys",0700,0,0));need(sc(39,(long)".codex-env",0700,0,0));
 need(mountfs("sysfs",".codex-sys","sysfs",15,0));
 if(!read_equals(".codex-sys/class/block/mmcblk0p3/dev","179:3\n") ||
    !read_equals(".codex-sys/class/block/mmcblk0p3/start","45056\n") ||
    !read_equals(".codex-sys/class/block/mmcblk0p3/size","32768\n"))fail();
 need(sc(14,(long)".codex-env-block",0x6180,0xb303,0));
 need(mountfs(".codex-env-block",".codex-env","ext4",14,0));
 long fs[32];need(sc(99,(long)".codex-env",(long)fs,0,0));
 if((U)fs[0]!=0xef53 || (fs[11]&1))fail();
 long mf=open_existing_env(".codex-env/uboot.env");
 long bf=open_existing_env(".codex-env/uboot.env.bak");
 read_exact_env(mf,main_before);read_exact_env(bf,bak_before);
 /* Both must independently match the exact trial. No guessed backup. */
 if(!env_restore_exact(main_before,main_after) || !env_restore_exact(bak_before,bak_after))fail();
 emit("<6>CTV_ENV_ONCE_BOTH_EXACT_TRIAL_PREFLIGHT\n");
 commit_existing_env(bf,bak_after);
 emit("<6>CTV_ENV_ONCE_BACKUP_FLUSHED_READBACK_VERIFIED\n");
 commit_existing_env(mf,main_after);
 emit("<6>CTV_ENV_ONCE_MAIN_FLUSHED_READBACK_VERIFIED\n");
 need(sc(6,bf,0,0,0));need(sc(6,mf,0,0,0));
 need(sc(52,(long)".codex-env",0,0,0));need(sc(10,(long)".codex-env-block",0,0,0));
 need(sc(52,(long)".codex-sys",0,0,0));
 need(sc(40,(long)".codex-env",0,0,0));need(sc(40,(long)".codex-sys",0,0,0));
}
