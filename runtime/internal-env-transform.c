// SPDX-License-Identifier: Apache-2.0
/* Pure byte transform, no syscalls. Never writes the source buffer.
 * Caller must separately validate partition/files and handle durable writes.
 * This file alone cannot alter a device or start a system.
 */
typedef unsigned int U;
typedef unsigned char B;
#ifndef TRIAL_INIT
#define TRIAL_INIT "init=/local/tmp/rd-root/envread"
#endif
static U len(const char*s){U n=0;while(s[n])n++;return n;}
static int eq(const B*p,U n,const char*s){U k=len(s);if(k!=n)return 0;for(U i=0;i<n;i++)if(p[i]!=(B)s[i])return 0;return 1;}
static int prefix(const B*p,U n,const char*s){U k=len(s);if(k>n)return 0;for(U i=0;i<k;i++)if(p[i]!=(B)s[i])return 0;return 1;}
static U crc(const B*p,U n){U c=~0u;while(n--){c^=*p++;for(U k=0;k<8;k++)c=(c>>1)^(0xedb88320u&(0u-(c&1)));}return ~c;}
static int equalkey(const B*a,U na,const B*b,U nb){if(na!=nb)return 0;for(U i=0;i<na;i++)if(a[i]!=b[i])return 0;return 1;}
int env_restore_exact(const B*src,B*dst){
 if(src==dst)return 0;
 U oldcrc=(U)src[0]|((U)src[1]<<8)|((U)src[2]<<16)|((U)src[3]<<24);
 if(crc(src+4,65532)!=oldcrc)return 0;
 U pos=4,begin=0,end=0,tail=0,entries=0;
 while(pos<65535 && src[pos]){
  U start=pos,keyend=0;
  while(pos<65536 && src[pos]){if(src[pos]=='=' && !keyend)keyend=pos;pos++;}
  if(pos==65536 || !keyend || keyend==start || ++entries>1024)return 0;
  for(U prior=4;prior<start;){
   U k=prior;while(src[k]!='=')k++;
   if(equalkey(src+prior,k-prior,src+start,keyend-start))return 0;
   while(src[prior])prior++;prior++;
  }
  if(eq(src+start,keyend-start,"bootargs")){begin=keyend+1;end=pos;}
  pos++;
 }
 if(!begin || end<=begin || end-begin>2047 || pos>=65535 || src[pos] || src[pos+1])return 0;
 tail=pos+1;
 for(U i=tail;i<65536;i++)if(src[i])return 0;
 const char*from[]={"root=/dev/mmcblk0p42",TRIAL_INIT,"ro","loglevel=7","rdinit=/codex-no-rdinit","rootfstype=f2fs","rootflags=norecovery"};
 const char*to[]={"root=/dev/ram","init=/init","rw","loglevel=0","","",""};
 const char*keys[]={"root=","init=","loglevel=","rdinit=","rootfstype=","rootflags="};
 volatile U counts[7],kc[6];U out=begin;
 for(U k=0;k<7;k++)counts[k]=0;
 for(U k=0;k<6;k++)kc[k]=0;
 for(U i=0;i<begin;i++)dst[i]=src[i];
 for(U i=begin;i<end;){
  U first=i;while(i<end && src[i]!=' '){if(src[i]<33 || src[i]>126)return 0;i++;}
  U n=i-first;if(!n)return 0;
  for(U k=0;k<6;k++)if(prefix(src+first,n,keys[k]))kc[k]++;
  for(U k=0;k<4;k++)if(eq(src+first,n,to[k]))return 0;
  U match=7;for(U k=0;k<7;k++)if(eq(src+first,n,from[k])){counts[k]++;match=k;}
  U size=(match==7)?n:len(to[match]);
  if(size){
   if(out>begin)dst[out++]=' ';
   for(U k=0;k<size;k++)dst[out++]=(match==7)?src[first+k]:(B)to[match][k];
  }
  if(i<end){i++;if(i==end)return 0;}
 }
 for(U k=0;k<7;k++)if(counts[k]!=1)return 0;
 for(U k=0;k<6;k++)if(kc[k]!=1)return 0;
 /* Includes the bootargs NUL, all later entries, and the second final NUL. */
 for(U i=end;i<tail;i++)dst[out++]=src[i];
 while(out<65536)dst[out++]=0;
 U v=crc(dst+4,65532);for(U i=0;i<4;i++)dst[i]=(B)(v>>(8*i));
 return 1;
}
