// SPDX-License-Identifier: Apache-2.0
/* Reconstruct a locally assembled RAM root from read-only F2FS. Embedded
 * firmware-derived files are local inputs, not part of this source distribution.
 * RESTORE_ENV writes and verifies the two existing persistent uenv files before
 * handoff. There are no AVB calls, slot changes or firmware-image writes here.
 * Early failures reboot for serial recovery; they are not autonomous rollback.
 */
typedef unsigned int u32;
#ifndef ROOT_TMPFS_OPTIONS
#define ROOT_TMPFS_OPTIONS "mode=0755,size=64m"
#endif
#ifndef ROOT_READY_MESSAGE
#define ROOT_READY_MESSAGE "<6>CTV_STOCK_ROOT_EMBEDDED_RAMDISK_RECONSTRUCTED\n"
#endif
#ifndef ROOT_EXEC_MESSAGE
#define ROOT_EXEC_MESSAGE "<6>CTV_STOCK_ROOT_PIVOTED_TMPFS_EXEC_ORIGINAL_INIT\n"
#endif
struct row {const char *name; u32 mode,uid,gid,offset,size;};
extern const unsigned char stock_blob[];
#include "stock-root-table.h"
static long sc(long n,long a,long b,long c,long d) {
 register long r0 __asm__("r0")=a,r1 __asm__("r1")=b;
 register long r2 __asm__("r2")=c,r3 __asm__("r3")=d;
 register long r7 __asm__("r7")=n;
 __asm__ volatile("svc 0":"+r"(r0):"r"(r1),"r"(r2),"r"(r3),"r"(r7):"memory","cc");return r0;
}
static long mountfs(const char*s,const char*t,const char*f,long flags,const char*opts) {
 register long r0 __asm__("r0")=(long)s,r1 __asm__("r1")=(long)t;
 register long r2 __asm__("r2")=(long)f,r3 __asm__("r3")=flags,r4 __asm__("r4")=(long)opts;
 register long r7 __asm__("r7")=21;
 __asm__ volatile("svc 0":"+r"(r0):"r"(r1),"r"(r2),"r"(r3),"r"(r4),"r"(r7):"memory","cc");return r0;
}
static long length(const char*s){long n=0;while(s[n])n++;return n;}
static long logfd=-1;
static void emit(const char*s){if(logfd>=0)sc(4,logfd,(long)s,length(s),0);}
static void fail(void) {
 struct {long sec,nsec;} delay={5,0};
 emit("<6>CTV_STOCK_ROOT_FAILED_UART_UNDO_REQUIRED\n");
 sc(162,(long)&delay,0,0,0);
 sc(88,0xfee1dead,0x28121969,0x01234567,0);
 for(;;)sc(162,(long)&delay,0,0,0);
}
static void need(long r){if(r<0)fail();}
static void writeall(long fd,const unsigned char*p,u32 n) {
 while(n){long k=sc(4,fd,(long)p,n,0);if(k<=0 || (u32)k>n)fail();p+=k;n-=k;}
}
#ifdef INSPECT_ENV
#include "internal-env-inspect.h"
#endif
#ifdef RESTORE_ENV
#include "internal-env-once.h"
#endif
static void cmdline(void) {
 need(sc(39,(long)".codex-proc",0700,0,0));
 need(mountfs("proc",".codex-proc","proc",1|2|4|8,0));
 long f=sc(5,(long)".codex-proc/cmdline",0,0,0);need(f);
 char buf[4096];long n=sc(3,f,(long)buf,sizeof(buf)-1,0);need(sc(6,f,0,0,0));
 if(n<1 || n>=4095)fail();
 /* Report size only; boot arguments may contain device identity material. */
 char line[]="<6>CTV_CMDLINE_BYTES=0000\n";
 const long scale[4]={1000,100,10,1};
 long value=n,first=length("<6>CTV_CMDLINE_BYTES=");
 for(long k=0;k<4;k++) {
  char digit='0';while(value>=scale[k]){value-=scale[k];digit++;}
  line[first+k]=digit;
 }
 need(sc(4,logfd,(long)line,length(line),0));
 need(sc(52,(long)".codex-proc",0,0,0));need(sc(40,(long)".codex-proc",0,0,0));
}
void entry(long*stack) {
 if(sc(20,0,0,0,0)!=1){sc(1,90,0,0,0);for(;;){}}
 if(stack[0]!=1)fail();
 char**env=(char**)(stack+3);
 long fs[32];
 if(sc(99,(long)"/",(long)fs,0,0)<0 || (u32)fs[0]!=0xf2f52010 || !(fs[11]&1))fail();
 const char*root="/local/tmp/rd-root/scratch";
 need(mountfs("tmpfs",root,"tmpfs",2,ROOT_TMPFS_OPTIONS));
 need(sc(12,(long)root,0,0,0));
 /* All paths and offsets are fixed at build time from the exact stock B.
  * Directories first, then regular files, then links: no symlink traversal. */
 for(u32 i=0;i<ROW_COUNT;i++)if((rows[i].mode&0170000)==0040000)
  need(sc(39,(long)rows[i].name,0700,0,0));
 for(u32 i=0;i<ROW_COUNT;i++) {
  const struct row*r=&rows[i];u32 kind=r->mode&0170000;
  if(kind==0100000) {
   long f=sc(5,(long)r->name,1|64|128|131072,0600,0);need(f);
   writeall(f,stock_blob+r->offset,r->size);need(sc(6,f,0,0,0));
  } else if(kind==0120000)need(sc(83,(long)(stock_blob+r->offset),(long)r->name,0,0));
 }
 for(u32 i=0;i<ROW_COUNT;i++) {
  const struct row*r=&rows[i];need(sc(198,(long)r->name,r->uid,r->gid,0)); /* lchown32 */
  if((r->mode&0170000)!=0120000)need(sc(15,(long)r->name,r->mode&07777,0,0));
 }
 need(sc(14,(long)"dev/kmsg",0x2180,0x10b,0));
 logfd=sc(5,(long)"dev/kmsg",1,0,0);need(logfd);
 emit(ROOT_READY_MESSAGE);
#ifdef INSPECT_ENV
 inspect_env();
#endif
#ifdef RESTORE_ENV
 restore_trial_env();
#endif
 cmdline();
 need(sc(39,(long)".codex-oldroot",0700,0,0));
 /* pivot_root detaches userdata from / and allows original init to mount it
  * normally. The old executable mapping is released at successful execve. */
 need(mountfs(0,"/",0,16384|262144,0)); /* MS_REC | MS_PRIVATE */
 need(sc(218,(long)".",(long)".codex-oldroot",0,0));
 need(sc(12,(long)"/",0,0,0));
 need(sc(52,(long)"/.codex-oldroot",2,0,0));
 need(sc(40,(long)"/.codex-oldroot",0,0,0));
 if(sc(99,(long)"/",(long)fs,0,0)<0 || (u32)fs[0]!=0x01021994)fail();
 emit(ROOT_EXEC_MESSAGE);
 need(sc(10,(long)"/dev/kmsg",0,0,0));need(sc(6,logfd,0,0,0));logfd=-1;
 char*args[]={"/init",0};sc(11,(long)args[0],(long)args,(long)env,0);fail();
}
__attribute__((naked,noreturn)) void _start(void){__asm__ volatile("mov r0, sp\nb entry\n");}
