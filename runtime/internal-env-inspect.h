// SPDX-License-Identifier: Apache-2.0
/* Read-only, no-journal-replay inspection of the factory environment files.
 * Runs inside the newly built RAM root. Nothing read here is logged verbatim.
 */
static unsigned char envbuffer[65537];
static int same(const char*a,const char*b){while(*a && *a==*b){a++;b++;}return *a==*b;}
static int read_equals(const char*path,const char*value) {
 char b[80];long f=sc(5,(long)path,0,0,0);if(f<0)return 0;
 long n=sc(3,f,(long)b,79,0);sc(6,f,0,0,0);
 if(n<0 || n>=79)return 0;b[n]=0;return same(b,value);
}
static u32 env_crc(const unsigned char*p,u32 n) {
 u32 c=0xffffffff;
 while(n--){c^=*p++;for(u32 k=0;k<8;k++)c=(c>>1)^(0xedb88320u & (0u-(c&1)));}
 return ~c;
}
static int has_token(const unsigned char*p,u32 n,const char*t) {
 u32 len=length(t);
 for(u32 i=0;i+len<=n;i++)if(i==0 || p[i-1]==' ') {
  u32 j=0;while(j<len && p[i+j]==(unsigned char)t[j])j++;
  if(j==len && (i+len==n || p[i+len]==' '))return 1;
 }
 return 0;
}
static int inspect_one(const char*path) {
 long f=sc(5,(long)path,131072,0,0);if(f<0)return 0;
 u32 used=0;long k;
 while(used<sizeof(envbuffer)) {
  k=sc(3,f,(long)(envbuffer+used),sizeof(envbuffer)-used,0);
  if(k<0 || (u32)k>sizeof(envbuffer)-used){sc(6,f,0,0,0);return 0;}
  if(!k)break;used+=k;
 }
 sc(6,f,0,0,0);if(used!=65536)return 0;
 u32 expected=(u32)envbuffer[0]|((u32)envbuffer[1]<<8)|((u32)envbuffer[2]<<16)|((u32)envbuffer[3]<<24);
 if(env_crc(envbuffer+4,65532)!=expected)return 0;
 u32 count=0;int stock=0;u32 i=4;
 while(i<65535 && envbuffer[i]) {
  u32 start=i;while(i<65536 && envbuffer[i])i++;
  if(i==65536)return 0;
  const char*key="bootargs=";u32 j=0;while(j<9 && start+j<i && envbuffer[start+j]==(unsigned char)key[j])j++;
  if(j==9) {
   count++;const unsigned char*p=envbuffer+start+9;u32 n=i-start-9;
   stock=has_token(p,n,"root=/dev/ram") && has_token(p,n,"init=/init");
   for(u32 x=0;x+7<=n;x++)if((x==0 || p[x-1]==' ') && p[x]=='r' && p[x+1]=='d' && p[x+2]=='i' && p[x+3]=='n' && p[x+4]=='i' && p[x+5]=='t' && p[x+6]=='=')stock=0;
  }
  i++;
 }
 if(i>=65535 || envbuffer[i] || envbuffer[i+1] || count!=1)return 0;
 return stock?3:1;
}
static void inspect_env(void) {
 int sys=0,env=0,main=0,bak=0;const char*result="<6>CTV_ENV_RO_INSPECTION_UNAVAILABLE\n";
 need(sc(39,(long)".codex-sys",0700,0,0));need(sc(39,(long)".codex-env",0700,0,0));
 if(mountfs("sysfs",".codex-sys","sysfs",15,0)<0)goto cleanup;
 sys=1;
 if(!read_equals(".codex-sys/class/block/mmcblk0p3/dev","179:3\n") ||
    !read_equals(".codex-sys/class/block/mmcblk0p3/start","45056\n") ||
    !read_equals(".codex-sys/class/block/mmcblk0p3/size","32768\n"))goto cleanup;
 need(sc(14,(long)".codex-env-block",0x6180,0xb303,0));
 if(mountfs(".codex-env-block",".codex-env","ext4",15,"noload")<0) {
  need(sc(10,(long)".codex-env-block",0,0,0));goto cleanup;
 }
 env=1;
 long fs[32];need(sc(99,(long)".codex-env",(long)fs,0,0));
 if((u32)fs[0]!=0xef53 || !(fs[11]&1))fail();
 main=inspect_one(".codex-env/uboot.env");bak=inspect_one(".codex-env/uboot.env.bak");
 if(main && bak)result=(bak==3)?"<6>CTV_ENV_RO_BOTH_CRC_VALID_BACKUP_STOCK\n":"<6>CTV_ENV_RO_BOTH_CRC_VALID_BACKUP_NONSTOCK\n";
 else result="<6>CTV_ENV_RO_FILE_VALIDATION_FAILED\n";
cleanup:
 if(env){need(sc(52,(long)".codex-env",0,0,0));need(sc(10,(long)".codex-env-block",0,0,0));}
 if(sys)need(sc(52,(long)".codex-sys",0,0,0));
 need(sc(40,(long)".codex-env",0,0,0));need(sc(40,(long)".codex-sys",0,0,0));
 emit(result);
}
