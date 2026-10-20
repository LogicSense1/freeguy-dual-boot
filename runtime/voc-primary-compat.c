/* SPDX-License-Identifier: Apache-2.0 */
/* RAM-only, firmware-pinned primary HAL compatibility adapter.
 * Delegates the original primary HAL and changes only the builtin VOC input.
 * Android uses INDEX_MASK_4; OEM uses position mask 0xcc and FAR_MIC.
 * No global product change, firmware write, output callback replacement,
 * automatic recording, PCM file, network operation or global VOC DeInit.
 * Loader deployment MUST pin the complete original ELF and original VAD boot.
 */
typedef unsigned u32;
typedef unsigned short u16;
typedef __SIZE_TYPE__ usize;
typedef __INTPTR_TYPE__ isize;
struct module;
struct device;
struct stream;
struct config {u32 rate,channels,format;};
typedef int (*open_input_fn)(struct device*,int,u32,struct config*,struct stream**,int,const char*,int);
typedef void (*close_input_fn)(struct device*,struct stream*);
struct methods {int (*open)(const struct module*,const char*,struct device**);};
struct module {u32 tag;u16 module_api,hal_api;const char *id,*name,*author;struct methods *methods;void *dso;u32 reserved[25];};
struct device_common {u32 tag,version;struct module *module;u32 reserved[12];int (*close)(struct device*);};
struct device {
 struct device_common common;
 /* Includes legacy get_supported_devices and every pre-input callback.
  * Intentionally opaque: no output, volume or mute slot is rewritten. */
 void *untouched[13];
 open_input_fn open_input;
 close_input_fn close_input;
};
struct stream {
 void *get_rate,*set_rate;usize (*buffer_size)(const struct stream*);u32 (*get_channels)(const struct stream*);
 void *get_format,*set_format;int (*standby)(struct stream*);void *dump;
 u32 (*get_device)(const struct stream*);
 int (*set_device)(struct stream*,u32);
 int (*set_parameters)(struct stream*,const char*);
 char *(*get_parameters)(const struct stream*,const char*);
 void *add_effect,*remove_effect,*set_gain;isize (*read)(struct stream*,void*,usize);
};
#ifndef VOC_COMPAT_TEST
_Static_assert(sizeof(void*)==4,"Pinned ARM32 HAL only");
_Static_assert(sizeof(struct module)==128,"OEM module ABI");
_Static_assert(sizeof(struct device_common)==64,"OEM device ABI");
_Static_assert(__builtin_offsetof(struct device,open_input)==0x74,"OEM input vtable");
_Static_assert(__builtin_offsetof(struct device,close_input)==0x78,"OEM close vtable");
_Static_assert(__builtin_offsetof(struct stream,get_channels)==0xc,"OEM channel callback");
_Static_assert(__builtin_offsetof(struct stream,get_device)==0x20,"OEM device callback");
_Static_assert(__builtin_offsetof(struct stream,read)==0x3c,"OEM read callback");
_Static_assert(__builtin_offsetof(struct stream,set_parameters)==0x28,"OEM routing callback");
#endif
enum {BUILTIN=0x80000004u,FAR=0x80800000u,INDEX4=0x8000000fu,OEM4=0xccu,PCM16=1};
#ifdef VOC_COMPAT_TEST
/* Host pointers are 64-bit; private OEM bytes must not overlap that host vtable.
 * The ARM build above separately checks the actual 32-bit public ABI. */
enum {PLATFORM_OFFSET=0x300,STREAM_CONFIG_OFFSET=0x100,STREAM_FLAG_OFFSET=0x120};
#else
enum {PLATFORM_OFFSET=0xac,STREAM_CONFIG_OFFSET=0x70,STREAM_FLAG_OFFSET=0xc8};
#endif
struct context {
 struct device *dev;open_input_fn open;close_input_fn close;int(*close_device)(struct device*);
 struct stream *stream;u32(*channels)(const struct stream*);
 u32(*get_device)(const struct stream*);int(*set_device)(struct stream*,u32);
 int(*set_parameters)(struct stream*,const char*);char *(*get_parameters)(const struct stream*,const char*);
 usize (*buffer_size)(const struct stream*);int (*standby)(struct stream*);
 isize (*read)(struct stream*,void*,usize);u32 owned;int ready;
};
static struct context contexts[4];
static unsigned char mutex_storage[64] __attribute__((aligned(8)));
static int (*mutex_lock)(void*),(*mutex_unlock)(void*);
static void *(*allocate)(usize);
static void (*deallocate)(void*);
static int (*voc_setup)(struct device*);
static int (*voc_stream_open)(struct device*,struct stream*);
static int (*sleep_us)(unsigned);
static u32 (*voc_query)(const void*,u32*),(*voc_open)(const void*,u32*),(*voc_close)(u32);
static struct module *original;
static unsigned char *bytes(void *p){return (unsigned char*)p;}
static struct context *find(struct device *d){for(unsigned i=0;i<4;i++)if(contexts[i].dev==d)return &contexts[i];return 0;}
static struct context *find_stream(const struct stream *s){for(unsigned i=0;i<4;i++)if(contexts[i].stream==s)return &contexts[i];return 0;}
static void clear_context(struct context *c){volatile unsigned char *p=(unsigned char*)c;for(usize i=0;i<sizeof(*c);i++)p[i]=0;}
static int any_owned(void){for(unsigned i=0;i<4;i++)if(contexts[i].owned||contexts[i].stream)return 1;return 0;}
static u32 standard_channels(const struct stream *s){(void)s;return INDEX4;}
static u32 standard_device(const struct stream *s){(void)s;return BUILTIN;}
static int set_standard_device(struct stream *s,u32 device){
 struct context *c=find_stream(s);if(!c||!c->set_device)return -19;
 return device==BUILTIN?c->set_device(s,FAR):-22;
}
/* Exact decimal routing values have equal length. Change only a whole value
 * of the routing key, never input_source, arbitrary vendor keys or prefixes. */
static void translate_routing(char *text,const char *from,const char *to){
 if(!text)return;
 for(usize i=0;text[i];i++){
  if(i&&text[i-1]!=';')continue;
  const char key[]="routing=";usize n=0;while(key[n]&&text[i+n]==key[n])n++;
  if(key[n])continue;
  usize j=0;while(from[j]&&text[i+n+j]==from[j])j++;
  if(from[j]||(text[i+n+j]&&text[i+n+j]!=';'))continue;
  for(usize k=0;k<j;k++)text[i+n+k]=to[k];
 }
}
static int set_standard_parameters(struct stream *s,const char *parameters){
 struct context *c=find_stream(s);if(!c||!c->set_parameters||!parameters)return -22;
 usize n=0;while(parameters[n]&&n<4096)n++;if(n==4096)return -22;
 char *copy=allocate(n+1);if(!copy)return -12;
 for(usize i=0;i<=n;i++)copy[i]=parameters[i];
 translate_routing(copy,"2147483652","2155872256");
 int result=c->set_parameters(s,copy);deallocate(copy);return result;
}
static char *get_standard_parameters(const struct stream *s,const char *keys){
 struct context *c=find_stream(s);if(!c||!c->get_parameters)return 0;
 char *result=c->get_parameters(s,keys);
 translate_routing(result,"2155872256","2147483652");return result;
}
static void restore_stream(struct context *c){
 c->stream->get_channels=c->channels;c->stream->get_device=c->get_device;
 c->stream->set_device=c->set_device;c->stream->set_parameters=c->set_parameters;
 c->stream->get_parameters=c->get_parameters;
 c->stream->buffer_size=c->buffer_size;c->stream->standby=c->standby;c->stream->read=c->read;
}
static int release_owned(struct context *c){
 unsigned char q=0;u32 current=0,status=voc_query(&q,&current);
 if(status==9&&!current){c->owned=0;return 0;}
 if(status||current!=c->owned)return -16;
 status=voc_close(c->owned);if(!status)c->owned=0;return status?-5:0;
}
/* Open/format negotiation never starts VOC. Only an actual read may do so.
 * The period is pinned to the 768-frame original GetAttr result for this board.
 * A differing runtime period fails closed instead of guessing buffer sizes. */
static usize standard_buffer_size(const struct stream *s){(void)s;return 768*4*2;}
static int start_owned(struct context *c){
 unsigned char q=0;u32 current=0,status=voc_query(&q,&current);
 if(c->owned)return status==0&&current==c->owned?0:-16;
 if(status!=9||current)return -16;
 struct device *d=c->dev;
 if(bytes(d)[0x5d4]||*(u32*)(bytes(d)+PLATFORM_OFFSET)!=1)return -1;
 if(!c->ready){
  if(!voc_setup(d))return -19;
  bytes(d)[0x5c4]=1;c->ready=1;
 }
 u32 params[5]={4,48000,0,0,0};status=voc_open(params,&c->owned);
 if(status||!c->owned){c->owned=0;return -5;}
 int result=voc_stream_open(d,c->stream);
 current=0;status=voc_query(&q,&current);
 if(result||status||current!=c->owned||*(u32*)(bytes(d)+0x5c8)!=768){
  int cleanup=release_owned(c);return cleanup?cleanup:-5;
 }
 bytes(c->stream)[STREAM_FLAG_OFFSET]=1;return 0;
}
static int stop_owned(struct context *c){
 bytes(c->stream)[STREAM_FLAG_OFFSET]=0;
 return c->owned?release_owned(c):0;
}
static isize standard_read(struct stream *s,void *buffer,usize size){
 struct context *c=find_stream(s);if(!c||!buffer||!size||size%8)return -22;
 mutex_lock(mutex_storage);
 /* Query the retained original mute callback on every read, fail closed on
  * errors; a muted read closes our VOC handle, emits zero and preserves pace. */
 int(*get_mute)(struct device*,unsigned char*)=(void*)c->dev->untouched[7];
 unsigned char muted=1;int result=get_mute?get_mute(c->dev,&muted):-19;
 if(result||muted){
  int cleanup=stop_owned(c);mutex_unlock(mutex_storage);
  if(result||cleanup)return result?result:cleanup;
  volatile unsigned char *p=buffer;for(usize i=0;i<size;i++)p[i]=0;
  sleep_us((unsigned)((size/8)*1000/48));return (isize)size;
 }
 result=start_owned(c);
 isize n=result?result:c->read(s,buffer,size);
 mutex_unlock(mutex_storage);return n;
}
static int standard_standby(struct stream *s){
 struct context *c=find_stream(s);if(!c)return -19;
 mutex_lock(mutex_storage);int cleanup=stop_owned(c);int rc=c->standby(s);
 mutex_unlock(mutex_storage);return cleanup?cleanup:rc;
}
static int compat_input(struct device *d,int io,u32 devices,struct config *cfg,struct stream **out,int flags,const char *address,int source){
 struct context *c=find(d);if(!c)return -19;
 if(devices!=BUILTIN)return c->open(d,io,devices,cfg,out,flags,address,source);
 if(!cfg||!out)return -22;*out=0;
 if(cfg->rate!=48000||cfg->channels!=INDEX4||cfg->format!=PCM16){
  cfg->rate=48000;cfg->channels=INDEX4;cfg->format=PCM16;return -22;
 }
 mutex_lock(mutex_storage);
 if(any_owned()){mutex_unlock(mutex_storage);return -16;}
 if(bytes(d)[0x5d4]||*(u32*)(bytes(d)+PLATFORM_OFFSET)!=1){mutex_unlock(mutex_storage);return -1;}
 /* The exact OEM open routine delegates FAR input as 48k stereo, then keeps
  * its four-channel VOC format in these private fields. Reproduce that setup
  * with VOC disabled, so policy enumeration cannot start microphone capture. */
 struct config saved=*cfg;cfg->channels=0xc;
 bytes(d)[0x5c4]=0;
 int result=c->open(d,io,FAR,cfg,out,flags,address,source);
 bytes(d)[0x5c4]=c->ready?1:0;*cfg=saved;
 if(result||!*out){mutex_unlock(mutex_storage);return result?result:-5;}
 if(!(*out)->read||!(*out)->standby){c->close(d,*out);*out=0;mutex_unlock(mutex_storage);return -19;}
 *(u32*)(bytes(*out)+STREAM_CONFIG_OFFSET)=48000;*(u32*)(bytes(*out)+STREAM_CONFIG_OFFSET+4)=OEM4;*(u32*)(bytes(*out)+STREAM_CONFIG_OFFSET+8)=PCM16;
 c->stream=*out;c->channels=(*out)->get_channels;c->get_device=(*out)->get_device;
 c->set_device=(*out)->set_device;c->set_parameters=(*out)->set_parameters;c->get_parameters=(*out)->get_parameters;
 c->buffer_size=(*out)->buffer_size;c->standby=(*out)->standby;c->read=(*out)->read;
 (*out)->get_channels=standard_channels;(*out)->get_device=standard_device;
 (*out)->set_device=set_standard_device;(*out)->set_parameters=set_standard_parameters;
 (*out)->get_parameters=get_standard_parameters;(*out)->buffer_size=standard_buffer_size;
 (*out)->standby=standard_standby;(*out)->read=standard_read;
 mutex_unlock(mutex_storage);return 0;
}
static void compat_close_input(struct device *d,struct stream *s){
 struct context *c=find(d);if(!c)return;
 if(s!=c->stream){c->close(d,s);return;}
 mutex_lock(mutex_storage);
 unsigned char q=0;u32 current=0,status=voc_query(&q,&current);
 restore_stream(c);
 if(!c->owned||status||current!=c->owned)bytes(s)[STREAM_FLAG_OFFSET]=0;
 c->close(d,s);c->stream=0;c->channels=0;
 /* OEM closes on stream close. Re-query, close only our residual handle. */
 if(c->owned)release_owned(c);
 mutex_unlock(mutex_storage);
}
static int compat_close_device(struct device *d){
 struct context *c=find(d);if(!c)return -19;
 mutex_lock(mutex_storage);
 if(c->stream||c->owned){mutex_unlock(mutex_storage);return -16;}
 int(*fn)(struct device*)=c->close_device;
 d->open_input=c->open;d->close_input=c->close;d->common.close=fn;
 /* OEM module close may DeInit a global VOC service; restore the feature flag
  * to its original ladybird value after all owned input streams were closed. */
 bytes(d)[0x5c4]=0;clear_context(c);
 int result=fn(d);mutex_unlock(mutex_storage);return result;
}
static int compat_open(const struct module *m,const char *id,struct device **out){
 (void)m;if(!original||!out)return -19;
 mutex_lock(mutex_storage);
 struct context *c=0;for(unsigned i=0;i<4;i++)if(!contexts[i].dev){c=&contexts[i];break;}
 if(!c){mutex_unlock(mutex_storage);return -16;}
 int result=original->methods->open(original,id,out);
 if(result||!*out){mutex_unlock(mutex_storage);return result?result:-5;}
 struct device *d=*out;
 /* Refuse a different OEM layout or already-enabled product behavior. */
 if(*(u32*)(bytes(d)+PLATFORM_OFFSET)!=1||bytes(d)[0x5c4]||!d->open_input||!d->close_input||!d->common.close){
  if(d->common.close)d->common.close(d);*out=0;mutex_unlock(mutex_storage);return -19;
 }
 c->dev=d;c->open=d->open_input;c->close=d->close_input;c->close_device=d->common.close;
 d->open_input=compat_input;d->close_input=compat_close_input;d->common.close=compat_close_device;
 mutex_unlock(mutex_storage);return 0;
}
static struct methods compat_methods={compat_open};
__attribute__((visibility("default"))) struct module HMI;
__attribute__((visibility("default"))) int codex_voc_compat_ready;
#ifndef VOC_COMPAT_TEST
extern void *dlopen(const char*,int);
extern void *dlsym(void*,const char*);
__attribute__((constructor)) static void load(void){
 /* Preserve the original module as fallback if optional input setup fails. */
 void *h=dlopen("/vendor/lib/hw/audio.primary.codex-original.so",2);if(!h)return;
 original=dlsym(h,"HMI");
 if(!original||original->tag!=0x48574d54||!original->methods||!original->methods->open)return;
 volatile unsigned char *to=(unsigned char*)&HMI;
 const volatile unsigned char *from=(const unsigned char*)original;
 for(usize i=0;i<sizeof(HMI);i++)to[i]=from[i];
 void *libc=dlopen("libc.so",2);if(!libc)return;
 mutex_lock=dlsym(libc,"pthread_mutex_lock");mutex_unlock=dlsym(libc,"pthread_mutex_unlock");
 allocate=dlsym(libc,"malloc");deallocate=dlsym(libc,"free");
 int(*init)(void*,const void*)=dlsym(libc,"pthread_mutex_init");
 if(!mutex_lock||!mutex_unlock||!allocate||!deallocate||!init||init(mutex_storage,0))return;
 voc_setup=dlsym(h,"mitv_voc_init");voc_stream_open=dlsym(h,"mitv_adev_open_voc");
 sleep_us=dlsym(libc,"usleep");
 void *voc=dlopen("/vendor/lib/libmtkvoc.so",2);if(!voc)return;
 voc_query=dlsym(voc,"a_mtktvapi_voc_MI_VOC_GetHandle");
 voc_open=dlsym(voc,"a_mtktvapi_voc_MI_VOC_Open");voc_close=dlsym(voc,"a_mtktvapi_voc_MI_VOC_Close");
 if(!original||!original->methods||!original->methods->open||!voc_setup||!voc_stream_open||!sleep_us||!voc_query||!voc_open||!voc_close)return;
 HMI.methods=&compat_methods;
 codex_voc_compat_ready=1;
}
#endif
