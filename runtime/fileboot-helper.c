/* SPDX-License-Identifier: Apache-2.0 */
/* ARM EABI static init dispatcher, policy loader, handover and bounded watchdog.
 * The daily watchdog writes the shared persistent fault flag before reboot.
 * No flash, erase, firmware-image write or network API.
 */
typedef unsigned int u32;
#include "watchdog-policy.h"
static long sc(long n,long a,long b,long c,long d) {
 register long r0 __asm__("r0")=a,r1 __asm__("r1")=b;
 register long r2 __asm__("r2")=c,r3 __asm__("r3")=d;
 register long r7 __asm__("r7")=n;
 __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r2),"r"(r3),"r"(r7) : "memory","cc");
 return r0;
}
static int eq(const char*a,const char*b) {while(*a && *a==*b){a++;b++;}return *a==*b;}
static long len(const char*s){long n=0;while(s[n])n++;return n;}
static void message(const char*s){sc(4,2,(long)s,len(s),0);}
static void die(long code){sc(1,code,0,0,0);for(;;){}}
static char data[1048576];
static long read_seconds(const char *path) {
 long fd=sc(5,(long)path,0,0,0);char value[32];long number=0;
 if(fd<0)return -1;
 long n=sc(3,fd,(long)value,31,0);sc(6,fd,0,0,0);
 if(n<1 || value[0]<'0' || value[0]>'9')return -1;
 for(long i=0;i<n;i++) {
  if(value[i]<'0' || value[i]>'9')break;
  if(number>100000000)return -1;
  number=number*10+value[i]-'0';
 }
 return number;
}
static long monotonic_seconds(void) {
 struct {long sec,nsec;} value;
 if(sc(263,1,(long)&value,0,0)<0 || value.sec<0 || value.nsec<0 || value.nsec>=1000000000)return -1;
 return value.sec;
}
static void print_seconds(long value) {
 char b[32];long n=31;b[n]='\n';
 do{b[--n]='0'+value%10;value/=10;}while(value>0);
 if(sc(4,1,(long)(b+n),32-n,0)!=32-n)die(78);
}
/* ARM EABI stat64 metadata offsets, verified against Linux UAPI asm/stat.h.
 * Keep checks local to the fixed, owned fault file; never open a block device.
 */
static int owned_stat(long fd,int directory) {
 u32 st[32];
 if(sc(197,fd,(long)st,0,0)<0)return 0;
 if(st[4]!=(u32)(directory?0040700:0100600) || st[6]!=1000 || st[7]!=1000)return 0;
 return directory || (st[5]==1 && st[12]==3 && st[13]==0);
}
static long daily_fault_open(void) {
 const char*parent="/fileboot-host/local/codex-system-default-v1";
 long dir=sc(5,(long)parent,040000|0100000,0,0);
 if(dir<0)return -1;
 if(!owned_stat(dir,1)){sc(6,dir,0,0,0);return -1;}
 long fd=sc(322,dir,(long)"fault",2|0100000,0);sc(6,dir,0,0,0);
 char value[4];
 if(fd<0)return -1;
 if(!owned_stat(fd,0) || sc(3,fd,(long)value,4,0)!=3 || value[0]!='O'||value[1]!='K'||value[2]!='\n'){
  sc(6,fd,0,0,0);return -1;
 }
 return fd;
}
static int daily_fault_latch(long fd) {
 char value[3];
 if(sc(19,fd,0,0,0)!=0 || sc(4,fd,(long)"F",1,0)!=1 || sc(118,fd,0,0,0)<0)return 0;
 if(sc(19,fd,0,0,0)!=0 || sc(3,fd,(long)value,3,0)!=3)return 0;
 return value[0]=='F'&&value[1]=='K'&&value[2]=='\n';
}
static long bind_mount(const char*source,const char*target,long flags) {
 register long r0 __asm__("r0")=(long)source,r1 __asm__("r1")=(long)target;
 register long r2 __asm__("r2")=0,r3 __asm__("r3")=flags,r4 __asm__("r4")=0;
 register long r7 __asm__("r7")=21;
 __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r2),"r"(r3),"r"(r4),"r"(r7) : "memory","cc");
 return r0;
}
void main_entry(long *stack) {
 long argc=*stack;char **argv=(char**)(stack+1);char **env=argv+argc+1;
 if(argc==2 && eq(argv[1],"monotonic-seconds")) {
  long now=monotonic_seconds();if(now<0)die(78);print_seconds(now);die(0);
 }
 if(argc==2 && eq(argv[1],"watchdog-daily")) {
  struct {long sec,nsec;} delay={0,100000000};
  long fault=daily_fault_open(),start=monotonic_seconds();
  if(fault<0||start<0)die(79);
  long protect=sc(5,(long)"/proc/self/oom_score_adj",1,0,0);
  if(protect>=0){sc(4,protect,(long)"-1000",5,0);sc(6,protect,0,0,0);}
  long ready=sc(5,(long)"/dev/codex-daily-watchdog-ready",1|64|512,0644,0);
  if(ready<0||sc(4,ready,(long)"ready\n",6,0)!=6)die(79);
  sc(6,ready,0,0,0);
  message("codex daily watchdog: monotonic startup 600s, health lease 120s, fault latched before reboot\n");
  int tick=0,seen=0,reason=0;
  for(;;) {
   if(tick==0){
    long now=monotonic_seconds(),heartbeat=read_seconds("/dev/codex-health-heartbeat");
    reason=watchdog_daily_reason(now,start,heartbeat,seen);
    if(reason)break;
    if(heartbeat>0&&heartbeat<=now&&now-heartbeat<=120&&!seen){seen=1;message("codex daily watchdog: healthy lease accepted\n");}
   }
   tick=(tick+1)%10;sc(162,(long)&delay,0,0,0);
  }
  if(!daily_fault_latch(fault)){
   message("codex daily watchdog: fault persistence failed; automatic reboot suppressed\n");
   /* No restart loop if durable recovery state cannot be recorded. */
   delay.sec=30;delay.nsec=0;for(;;)sc(162,(long)&delay,0,0,0);
  }
  sc(6,fault,0,0,0);message("codex daily watchdog: fault recorded, returning to stock\n");
  sc(36,0,0,0,0);sc(88,0xfee1dead,672274793,0x01234567,0);die(30);
 }
 if(argc>1 && eq(argv[1],"bind-recursive")) {
  if(argc!=4)die(66);
  if(bind_mount(argv[2],argv[3],4096|16384)<0)die(67);
  die(0);
 }
 if(argc>1 && eq(argv[1],"bind-file")) {
  if(argc!=4)die(64);
  if(bind_mount(argv[2],argv[3],4096)<0)die(65);
  die(0);
 }
 if(argc>1 && eq(argv[1],"bind-readonly")) {
  if(argc!=4)die(60);
  long fd=sc(5,(long)argv[2],0,0,0);int ro=0;
  if(fd<0)die(61);
  if(sc(54,fd,0x125e,(long)&ro,0)<0 || ro!=1)die(62);
  long rc=bind_mount(argv[2],argv[3],4096);sc(6,fd,0,0,0);
  if(rc<0)die(63);
  die(0);
 }
 if(argc>1 && eq(argv[1],"policy")) {
  if(argc!=3)die(20);
  long fd=sc(5,(long)argv[2],0,0,0),size=0,n;
  if(fd<0)die(21);
  while(size<(long)sizeof(data)) {n=sc(3,fd,(long)(data+size),sizeof(data)-size,0);if(n<0)die(22);if(!n)break;size+=n;}
  sc(6,fd,0,0,0);
  if(size<100000 || size==(long)sizeof(data) || *(u32*)data!=0xf97cff8c)die(23);
  fd=sc(5,(long)"/sys/fs/selinux/load",1,0,0);if(fd<0)die(24);
  n=sc(4,fd,(long)data,size,0);sc(6,fd,0,0,0);if(n!=size)die(25);
  fd=sc(5,(long)"/sys/fs/selinux/enforce",1,0,0);if(fd<0)die(26);
  n=sc(4,fd,(long)"0",1,0);sc(6,fd,0,0,0);if(n!=1)die(27);
  die(0);
 }
 if(argc>1 && eq(argv[1],"kernel-log")) {
  long fd=sc(5,(long)"/dev/kmsg",2048,0,0),total=0;
  struct {long sec,nsec;} delay={0,100000000};
  if(fd<0)die(31);
  /* Diagnostic storage can block. Never run this in the deadline process. */
  for(int t=0;t<40000 && total<32*1024*1024;t++) {
   int wrote=0;
   for(int i=0;i<64 && total<32*1024*1024;i++) {
    long n=sc(3,fd,(long)data,8192,0);if(n<0)break;
    if(n>0){sc(4,1,(long)data,n,0);total+=n;wrote=1;}else break;
   }
   if(wrote)sc(118,1,0,0,0);
   sc(162,(long)&delay,0,0,0);
  }
  sc(6,fd,0,0,0);die(0);
 }
 if(argc>1 && eq(argv[1],"watchdog")) {
  struct {long sec,nsec;} delay={0,100000000};
  long protect=sc(5,(long)"/proc/self/oom_score_adj",1,0,0);
  if(protect>=0){sc(4,protect,(long)"-1000",5,0);sc(6,protect,0,0,0);}
  long start=read_seconds("/proc/uptime");int seen=0,reason=0;
  message("codex fileboot watchdog: startup 600s, health lease 120s, absolute cap 3600s\n");
  for(int t=0;t<40000;t++) {
   if(t%10==0) {
    long now=read_seconds("/proc/uptime");
    long heartbeat=read_seconds("/dev/codex-health-heartbeat");
    if(start<0 || now<0){reason=4;break;}
    reason=watchdog_reason(now,start,heartbeat,seen);
    if(reason)break;
    if(heartbeat>0 && heartbeat<=now && now-heartbeat<=120 && !seen) {
     seen=1;message("codex fileboot watchdog: healthy Android lease accepted\n");
    }
   }
   sc(162,(long)&delay,0,0,0);
  }
  if(reason==1)message("codex fileboot watchdog: startup deadline\n");
  if(reason==2)message("codex fileboot watchdog: health lease expired\n");
  if(reason==3)message("codex fileboot watchdog: absolute trial deadline\n");
  if(reason==4)message("codex fileboot watchdog: uptime read failed\n");
  message("codex fileboot watchdog: syncing and restarting stock boot\n");
  sc(36,0,0,0,0);
  sc(88,0xfee1dead,672274793,0x01234567,0);
  die(30);
 }
 if(argc>1 && eq(argv[1],"observe")) {
  if(argc<4)die(70);
  if(sc(61,(long)argv[2],0,0,0)<0 || sc(12,(long)"/",0,0,0)<0)die(71);
  /* Keep an early observer in the bootstrap namespace. Runtime APEX and
   * linkerconfig are mounted there before executing the native shell. */
  struct {long sec,nsec;} delay={15,0};
  sc(162,(long)&delay,0,0,0);
  sc(11,(long)argv[3],(long)(argv+3),(long)env,0);die(72);
 }
 if(argc>1 && eq(argv[1],"handoff")) {
  if(argc!=3)die(40);
  /* Follow Android SwitchRoot: move the root mount, not merely chroot.
   * setns(CLONE_NEWNS) otherwise resets fs.root to the old recovery root. */
  if(sc(12,(long)argv[2],0,0,0)<0)die(41);
  if(bind_mount(argv[2],"/",8192)<0)die(43); /* MS_MOVE */
  if(sc(61,(long)".",0,0,0)<0 || sc(12,(long)"/",0,0,0)<0)die(44);
  /* Exercise the same namespace round trip before executing native init. */
  long ns=sc(5,(long)"/proc/self/ns/mnt",0,0,0);
  if(ns<0 || sc(337,0x20000,0,0,0)<0 || sc(375,ns,0x20000,0,0)<0)die(45);
  sc(6,ns,0,0,0);
  long check=sc(5,(long)"/system/bin/bootstrap/linker",0,0,0);
  if(check<0)die(46);
  sc(6,check,0,0,0);
  message("codex: root mount moved; native namespace round trip verified\n");
  sc(118,2,0,0,0);
  char *args[]={"/system/bin/init","second_stage",0};
  sc(11,(long)args[0],(long)args,(long)env,0);die(42);
 }
 if(argc==2 && eq(argv[1],"second_stage")) {
  char *args[]={"/system/bin/sh","/codex/entry.sh",0};
  sc(11,(long)args[0],(long)args,(long)env,0);die(50);
 }
 /* Retain argv[0]: stock init dispatches ueventd using its invocation name. */
 sc(11,(long)"/system/bin/stock-init",(long)argv,(long)env,0);
 message("codex dispatcher failed to execute stock init\n");die(51);
}
__attribute__((naked,noreturn)) void _start(void) {
 __asm__ volatile("mov r0, sp\nb main_entry\n");
}
