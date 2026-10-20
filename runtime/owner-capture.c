/* SPDX-License-Identifier: Apache-2.0
 * Offline candidate: runs only as PID 1 in a reconstructed stock RAM root.
 * Read four fixed partition devices into NEW ordinary userdata files, then
 * hand off to the unmodified stock init. No partition write descriptors.
 * The outer loader must already have removed its temporary boot arguments.
 * Not a standalone recovery program or permission to deploy this candidate.
 */
typedef unsigned int u32;
typedef unsigned long long u64;
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
static long label_fd(long fd) {
 static const char label[]="u:object_r:shell_data_file:s0";
 register long r0 __asm__("r0")=fd,r1 __asm__("r1")=(long)"security.selinux";
 register long r2 __asm__("r2")=(long)label,r3 __asm__("r3")=sizeof(label),r4 __asm__("r4")=0;
 register long r7 __asm__("r7")=228; /* fsetxattr, only new ordinary output files/directories */
 __asm__ volatile("svc 0":"+r"(r0):"r"(r1),"r"(r2),"r"(r3),"r"(r4),"r"(r7):"memory","cc");return r0;
}
static u32 length(const char*s){u32 n=0;while(s[n])n++;return n;}
static int equal(const char*a,const char*b){while(*a && *a==*b){a++;b++;}return *a==*b;}
static int read_equal(const char*p,const char*expected) {
 char b[80];long f=sc(5,(long)p,131072,0,0);if(f<0)return 0;
 long n=sc(3,f,(long)b,79,0);long close=sc(6,f,0,0,0);
 if(n<0 || n>=79 || close<0)return 0;b[n]=0;return equal(b,expected);
}
static int directory(const char*p) {
 unsigned char b[104];if(sc(196,(long)p,(long)b,0,0)<0)return 0;
 u32 mode=(u32)b[16]|((u32)b[17]<<8)|((u32)b[18]<<16)|((u32)b[19]<<24);
 return (mode&0170000)==0040000;
}
static int writeall(long f,const unsigned char*p,u32 n) {
 while(n){long k=sc(4,f,(long)p,n,0);if(k<=0 || (u32)k>n)return 0;p+=k;n-=k;}return 1;
}
__attribute__((noinline)) u32 crc(u32 c,const unsigned char*p,u32 n) {
 static u32 table[256];static int ready=0;
 if(!ready){for(u32 i=0;i<256;i++){u32 v=i;for(u32 k=0;k<8;k++)v=(v>>1)^(0xedb88320u&(0u-(v&1)));table[i]=v;}ready=1;}
 while(n--)c=table[(c^*p++)&255]^(c>>8);
 return c;
}
static unsigned char buffer[65536];
static unsigned char boot_id[37];
static int read_boot_id(void) {
 unsigned char value[38];long f=sc(5,(long)"/owner-proc/sys/kernel/random/boot_id",131072,0,0);
 if(f<0)return 0;long n=sc(3,f,(long)value,sizeof(value),0);long closed=sc(6,f,0,0,0);
 if(n!=37 || closed<0 || value[36]!='\n')return 0;
 for(u32 i=0;i<36;i++) {
  if(i==8 || i==13 || i==18 || i==23){if(value[i]!='-')return 0;}
  else if(!((value[i]>='0' && value[i]<='9') || (value[i]>='a' && value[i]<='f')))return 0;
 }
 for(u32 i=0;i<37;i++)boot_id[i]=value[i];return 1;
}
static const struct source {
 const char *devpath,*startpath,*sizepath,*dev,*start,*sectors,*node,*output;
 u32 encoded,bytes;
} sources[]={
 {"/owner-sys/class/block/mmcblk0p3/dev","/owner-sys/class/block/mmcblk0p3/start",
  "/owner-sys/class/block/mmcblk0p3/size","179:3\n","45056\n","32768\n",
  "/owner-p3","/owner-host/local/tmp/freeguy-owner-capture-v1/uenv.img",0xb303,16777216},
 {"/owner-sys/class/block/mmcblk0p4/dev","/owner-sys/class/block/mmcblk0p4/start",
  "/owner-sys/class/block/mmcblk0p4/size","179:4\n","77824\n","32768\n",
  "/owner-p4","/owner-host/local/tmp/freeguy-owner-capture-v1/uuenv.img",0xb304,16777216},
 {"/owner-sys/class/block/mmcblk0p18/dev","/owner-sys/class/block/mmcblk0p18/start",
  "/owner-sys/class/block/mmcblk0p18/size","259:10\n","1310720\n","40960\n",
  "/owner-p18","/owner-host/local/tmp/freeguy-owner-capture-v1/persist.img",0x1030a,20971520},
 {"/owner-sys/class/block/mmcblk0p5/dev","/owner-sys/class/block/mmcblk0p5/start",
  "/owner-sys/class/block/mmcblk0p5/size","179:5\n","110592\n","65536\n",
  "/owner-p5","/owner-host/local/tmp/freeguy-owner-capture-v1/tvcertificate.img",0xb305,33554432}
};
static int copy_one(const struct source*s,u32*outcrc) {
 long input=-1,output=-1;int ok=0;u32 first=0;
 for(u32 pass=0;pass<2;pass++) {
  input=sc(5,(long)s->node,131072,0,0);if(input<0)goto done;
  if(pass==0) {
   output=sc(5,(long)s->output,1|64|128|131072,0600,0);if(output<0)goto done;
  }
  u32 used=0,c=0xffffffff;
  while(used<s->bytes) {
   u32 want=s->bytes-used;if(want>sizeof(buffer))want=sizeof(buffer);
   long n=sc(3,input,(long)buffer,want,0);
   if(n<=0 || (u32)n>want)goto done;
   /* Validate the superblock magic across partial reads, without seeking. */
   if(used<=1080 && used+(u32)n>1080 && buffer[1080-used]!=0x53)goto done;
   if(used<=1081 && used+(u32)n>1081 && buffer[1081-used]!=0xef)goto done;
   c=crc(c,buffer,(u32)n);
   if(pass==0 && !writeall(output,buffer,(u32)n))goto done;
   used+=(u32)n;
  }
  if(sc(3,input,(long)buffer,1,0)!=0)goto done;
  if(sc(6,input,0,0,0)<0){input=-1;goto done;}input=-1;
  if(pass==0) {
   first=~c;
   if(sc(207,output,2000,2000,0)<0 || label_fd(output)<0 || sc(118,output,0,0,0)<0)goto done;
   if(sc(6,output,0,0,0)<0){output=-1;goto done;}output=-1;
  } else if(~c!=first)goto done;
 }
 *outcrc=first;ok=1;
done:
 if(input>=0)sc(6,input,0,0,0);
 if(output>=0)sc(6,output,0,0,0);
 return ok;
}
static int capture(void) {
 int sys=0,host=0,proc=0,ok=0;long dir=-1;u32 values[4],fs[32];
 const char*destination="/owner-host/local/tmp/freeguy-owner-capture-v1";
 if(sc(39,(long)"/owner-sys",0700,0,0)<0 || sc(39,(long)"/owner-host",0700,0,0)<0)return 0;
 if(mountfs("sysfs","/owner-sys","sysfs",15,0)<0)return 0;sys=1;
 if(sc(39,(long)"/owner-proc",0700,0,0)<0 ||
    mountfs("proc","/owner-proc","proc",15,0)<0)goto done;
 proc=1;
 if(!read_boot_id())goto done;
 for(u32 i=0;i<4;i++) {
  const struct source*s=&sources[i];
  if(!read_equal(s->devpath,s->dev) || !read_equal(s->startpath,s->start) ||
     !read_equal(s->sizepath,s->sectors))goto done;
 }
 if(!read_equal("/owner-sys/class/block/mmcblk0p42/dev","259:34\n") ||
    !read_equal("/owner-sys/class/block/mmcblk0p42/start","9277440\n") ||
    !read_equal("/owner-sys/class/block/mmcblk0p42/size","112865240\n"))goto done;
 if(sc(14,(long)"/owner-p42",0060600,0x10322,0)<0)goto done;
 for(u32 i=0;i<4;i++)
  if(sc(14,(long)sources[i].node,0060600,sources[i].encoded,0)<0)goto done;
 /* Only userdata is mounted writable. Four source devices are read-only FDs. */
 if(mountfs("/owner-p42","/owner-host","f2fs",2|4,0)<0)goto done;host=1;
 if(sc(99,(long)"/owner-host",(long)fs,0,0)<0 || fs[0]!=0xf2f52010 ||
    (fs[11]&1) || (u64)fs[1]*fs[4] < 2ULL*1024*1024*1024+88080384)goto done;
 if(!directory("/owner-host/local") || !directory("/owner-host/local/tmp"))goto done;
 if(sc(39,(long)destination,0700,0,0)<0)goto done; /* Existing output always refuses. */
 dir=sc(5,(long)destination,65536|131072,0,0);if(dir<0)goto done;
 if(sc(207,dir,2000,2000,0)<0 || label_fd(dir)<0 || sc(118,dir,0,0,0)<0)goto done;
 for(u32 i=0;i<4;i++)if(!copy_one(&sources[i],&values[i]))goto done;
 /* Host verification checks SHA-256, owner binding and environment CRCs. */
 unsigned char marker[128];u32 n=0;const char*prefix="FREEGUY_OWNER_CAPTURE_V1\n";
 for(u32 j=0;prefix[j];j++)marker[n++]=prefix[j];
 for(u32 j=0;j<37;j++)marker[n++]=boot_id[j];
 for(u32 i=0;i<4;i++){
  for(int shift=28;shift>=0;shift-=4)marker[n++]="0123456789abcdef"[(values[i]>>shift)&15];
  marker[n++]='\n';
 }
 long f=sc(5,(long)"/owner-host/local/tmp/freeguy-owner-capture-v1/COMPLETE",
           1|64|128|131072,0600,0);
 if(f<0)goto done;
 ok=writeall(f,marker,n) && sc(207,f,2000,2000,0)>=0 && label_fd(f)>=0 && sc(118,f,0,0,0)>=0;
 if(sc(6,f,0,0,0)<0)ok=0;
 if(sc(118,dir,0,0,0)<0)ok=0;
done:
 if(dir>=0 && sc(6,dir,0,0,0)<0)ok=0;
 if(host && sc(52,(long)"/owner-host",0,0,0)<0)return -1;
 if(proc && sc(52,(long)"/owner-proc",0,0,0)<0)return -1;
 if(sys && sc(52,(long)"/owner-sys",0,0,0)<0)return -1;
 return ok;
}
void entry(long*stack) {
 if(sc(20,0,0,0,0)!=1){sc(1,90,0,0,0);for(;;){}}
 u32 fs[32];int ok=0;
 if(stack[0]!=1 || sc(99,(long)"/",(long)fs,0,0)<0 || fs[0]!=0x01021994)goto restart;
 ok=capture();
 /* The original init starts even if capture is refused or incomplete. */
 char *args[]={"/init",0};char**env=(char**)(stack+3);
 int lognode=sc(14,(long)"/owner-kmsg",0020600,0x10b,0)>=0;
 long fd=lognode?sc(5,(long)"/owner-kmsg",1|131072,0,0):-1;
 if(fd>=0){const char*s=ok==1?"<6>FREEGUY_OWNER_CAPTURE_COMPLETE\n":"<6>FREEGUY_OWNER_CAPTURE_INCOMPLETE\n";
  writeall(fd,(const unsigned char*)s,length(s));sc(6,fd,0,0,0);}
 if(lognode)sc(10,(long)"/owner-kmsg",0,0,0);
 /* Restore the original init at its exact RAM path before executing it.
  * A later init re-exec must never re-enter this collector. */
 if(ok>=0 && sc(38,(long)"/system/bin/stock-init",(long)"/system/bin/init",0,0)>=0)
  sc(11,(long)"/init",(long)args,(long)env,0);
 /* Unexpected init exec failure returns to the already restored stock entry. */
restart:
 sc(88,0xfee1dead,0x28121969,0x01234567,0);
 for(;;){struct {long sec,nsec;} delay={1,0};sc(162,(long)&delay,0,0,0);}
}
__attribute__((naked,noreturn)) void _start(void){__asm__ volatile("mov r0, sp\nb entry\n");}
