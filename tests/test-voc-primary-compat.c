/* SPDX-License-Identifier: Apache-2.0 */
/* Offline contract tests, using ASAN/UBSAN; no hardware or microphone. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define VOC_COMPAT_TEST 1
#include "../runtime/voc-primary-compat.c"
static u32 current;static int scenario,setup_calls,opens,closes,delegated,reads,closed_streams,closed_devices,locked,muted,mute_error;
static u32 last_device;static char received_parameters[128];
static void expect(int c){if(!c){fprintf(stderr,"FAIL case=%d\n",scenario);abort();}}
static int lock_test(void *p){(void)p;expect(!locked);locked=1;return 0;}
static int unlock_test(void *p){(void)p;expect(locked);locked=0;return 0;}
static int sleep_test(unsigned n){expect(n>0&&n<=100000);return 0;}
static int get_mute_test(struct device *d,unsigned char *m){(void)d;*m=muted;return mute_error?-19:0;}
static u32 query_test(const void *p,u32 *h){expect(!*(const unsigned char*)p);*h=current;return current?0:9;}
static int setup_test(struct device *d){(void)d;setup_calls++;return scenario!=4;}
static u32 open_test(const void *p,u32 *h){const u32 *c=p;expect(c[0]==4&&c[1]==48000&&!c[2]&&!c[3]&&!c[4]&&!current);opens++;if(scenario==5)return 1;*h=current=77;return 0;}
static u32 close_test(u32 h){expect(h==77&&current==h);closes++;current=0;return 0;}
static int stream_open_test(struct device *d,struct stream *s){
 expect(*(u32*)(bytes(s)+STREAM_CONFIG_OFFSET)==48000&&*(u32*)(bytes(s)+STREAM_CONFIG_OFFSET+4)==OEM4);
 expect(current==77&&bytes(d)[0x5c4]);*(u32*)(bytes(d)+0x5c8)=scenario==7?0:768;
 if(scenario==8)current=99;return 0;
}
static u32 oem_channels(const struct stream *s){(void)s;return OEM4;}
static u32 oem_device(const struct stream *s){(void)s;return FAR;}
static usize oem_buffer(const struct stream *s){(void)s;return 1024;}
static int oem_standby(struct stream *s){(void)s;return 0;}
static isize oem_read(struct stream *s,void *p,usize n){expect(current==77&&bytes(s)[STREAM_FLAG_OFFSET]);reads++;memset(p,1,n);return n;}
static int oem_set_device(struct stream *s,u32 d){(void)s;expect(d==FAR);return 0;}
static int oem_set_parameters(struct stream *s,const char *p){(void)s;snprintf(received_parameters,sizeof(received_parameters),"%s",p);return 0;}
static char *oem_get_parameters(const struct stream *s,const char *p){(void)s;(void)p;return strdup("other=2155872256;routing=2155872256;input_source=6");}
static int original_input(struct device *d,int io,u32 dev,struct config *c,struct stream **out,int flags,const char *addr,int source){
 expect(io==42&&flags==3&&!strcmp(addr,"test")&&source==6);delegated++;last_device=dev;
 if(scenario==6)return -22;
 *out=calloc(1,0x140);(*out)->get_channels=oem_channels;(*out)->get_device=oem_device;(*out)->set_device=oem_set_device;
 (*out)->set_parameters=oem_set_parameters;(*out)->get_parameters=oem_get_parameters;
 (*out)->buffer_size=oem_buffer;(*out)->standby=oem_standby;(*out)->read=oem_read;
 if(dev==FAR)expect(c->rate==48000&&c->channels==0xc&&c->format==PCM16&&!bytes(d)[0x5c4]);
 return 0;
}
static void original_close_input(struct device *d,struct stream *s){
 (void)d;closed_streams++;expect(s->get_channels==oem_channels&&s->get_device==oem_device&&s->set_device==oem_set_device&&s->set_parameters==oem_set_parameters&&s->get_parameters==oem_get_parameters&&s->buffer_size==oem_buffer&&s->standby==oem_standby&&s->read==oem_read);
 if(bytes(s)[STREAM_FLAG_OFFSET])close_test(current);free(s);
}
static int original_close_device(struct device *d){expect(!bytes(d)[0x5c4]);closed_devices++;free(d);return 0;}
static int original_open(const struct module *m,const char *id,struct device **out){
 (void)m;expect(!strcmp(id,"audio_hw_if"));*out=calloc(1,0x600);struct device *d=*out;
 d->common.close=original_close_device;d->open_input=original_input;d->close_input=original_close_input;
 for(unsigned i=0;i<13;i++)d->untouched[i]=(void*)(unsigned long)(0x100+i);
 d->untouched[7]=(void*)get_mute_test;*(u32*)(bytes(d)+PLATFORM_OFFSET)=1;return 0;
}
static struct methods original_methods={original_open};static struct module original_module={.methods=&original_methods};
int main(void){
 for(scenario=0;scenario<16;scenario++){
  memset(contexts,0,sizeof(contexts));current=0;setup_calls=opens=closes=delegated=reads=closed_streams=closed_devices=locked=muted=mute_error=0;
  original=&original_module;mutex_lock=lock_test;mutex_unlock=unlock_test;voc_query=query_test;voc_setup=setup_test;voc_open=open_test;voc_close=close_test;voc_stream_open=stream_open_test;sleep_us=sleep_test;allocate=malloc;deallocate=free;
  struct device *d=0;expect(!compat_open(0,"audio_hw_if",&d));
  for(unsigned i=0;i<13;i++)expect(d->untouched[i]==(i==7?(void*)get_mute_test:(void*)(unsigned long)(0x100+i)));
  struct config cfg={48000,INDEX4,PCM16};struct stream *stream=0;
  if(scenario==1)cfg.channels=0xc;if(scenario==2)current=99;if(scenario==9)bytes(d)[0x5d4]=1;if(scenario==10)*(u32*)(bytes(d)+PLATFORM_OFFSET)=0;
  u32 device=scenario==3?0x80000020:BUILTIN;
  int rc=d->open_input(d,42,device,&cfg,&stream,3,"test",6);
  expect(cfg.rate==48000&&cfg.channels==INDEX4&&cfg.format==PCM16);
  expect(!setup_calls&&!opens&&!reads); /* Including policy open: no capture. */
  if(scenario==1||scenario==6||scenario==9||scenario==10){expect(rc==(scenario==9||scenario==10?-1:-22)&&!stream);}
  else if(scenario==3){expect(!rc&&last_device==device);d->close_input(d,stream);stream=0;}
  else {
   expect(!rc&&stream&&stream->get_channels(stream)==INDEX4&&stream->buffer_size(stream)==6144);
   expect(stream->get_device(stream)==BUILTIN&&!stream->set_device(stream,BUILTIN)&&stream->set_device(stream,0x80000020)==-22);
   expect(!stream->set_parameters(stream,"input_source=6;routing=2147483652;other=2147483652"));expect(!strcmp(received_parameters,"input_source=6;routing=2155872256;other=2147483652"));
   char *p=stream->get_parameters(stream,"routing;other");expect(!strcmp(p,"other=2155872256;routing=2147483652;input_source=6"));free(p);
   struct stream *second=0;expect(d->open_input(d,42,BUILTIN,&cfg,&second,3,"test",6)==-16&&!second);expect(d->common.close(d)==-16);
   unsigned char buffer[6144];memset(buffer,55,sizeof(buffer));
   if(scenario==12)muted=1;if(scenario==13)mute_error=1;
   isize n=scenario==15?0:stream->read(stream,buffer,sizeof(buffer));
   int failure=scenario==2||scenario==4||scenario==5||scenario==7||scenario==8||scenario==13;
   if(scenario==15)expect(!opens&&!setup_calls); /* Open/close only stays inert. */
   else if(failure)expect(n<0&&!reads);
   else if(scenario==12){expect(n==sizeof(buffer)&&!opens&&!reads);for(unsigned i=0;i<sizeof(buffer);i++)expect(!buffer[i]);}
   else {
    expect(n==sizeof(buffer)&&reads==1&&opens==1&&current==77);
    if(scenario==11){expect(!stream->standby(stream)&&!current&&closes==1);expect(stream->read(stream,buffer,sizeof(buffer))==sizeof(buffer)&&opens==2&&setup_calls==1);}
    if(scenario==14){muted=1;expect(stream->read(stream,buffer,sizeof(buffer))==sizeof(buffer)&&!current&&closes==1&&reads==1);for(unsigned i=0;i<sizeof(buffer);i++)expect(!buffer[i]);}
   }
   d->close_input(d,stream);stream=0;
   if(scenario==8){expect(current==99&&!closes);expect(d->common.close(d)==-16);current=0;expect(!release_owned(find(d)));}
  }
  expect(!d->common.close(d)&&!locked&&closed_devices==1);
  if(scenario==2)expect(current==99&&!opens&&!closes);
  printf("case=%d PASS openStatus=%d reads=%d opens=%d closes=%d\n",scenario,rc,reads,opens,closes);
 }
 char *cases[]={"", "r", "routing", "routing=", "routing=214748365", "routing=21474836520", "foo=routing=2147483652", "routing=2147483652;routing=2147483652"};
 for(unsigned i=0;i<sizeof(cases)/sizeof(cases[0]);i++){char *p=strdup(cases[i]);translate_routing(p,"2147483652","2155872256");expect(!strcmp(p,i==7?"routing=2155872256;routing=2155872256":cases[i]));free(p);}
 puts("routing exact-key and truncated-input cases PASS");
}
