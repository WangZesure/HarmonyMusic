import {SwiperModel,SongListModel,SingerModel,RecommendSongModel,SongModel} from '../model/AppModel'
import {getSwiperList,getRecommendSongList,getRecommendSinger,getRecommendSongs} from '../common/Api'
import {ConfigUtil as Config} from '../common/ConfigUtil'
import router from '@ohos.router';
import { PlayStateModel } from '../model/PlayStateModel'
import emitter from '@ohos.events.emitter'
import { EmitEventType } from '../model/paramModel'
import { AvPlayerUtils } from '../utils/AvPlayerUtils'
import preferencesUtil from '../common/PreferencesUtil'

@Component
export struct Home{
  @State swiperList:SwiperModel[] = []
  @State songList:SongListModel[] = []
  @State recommendSongs:RecommendSongModel[] = [] // 新增：AI推荐单曲
  @State singerList:SingerModel[] = []
  // 用户信息是异步从 Preferences 读回来的（Index.initUser），Home 的 aboutToAppear
  // 往往先跑，此时 userInfo 还是空的，会退回 userId=3。这里监听变化后重新拉推荐。
  @StorageLink("userInfo") @Watch("onUserInfoChanged") userInfo: Record<string, ESObject> = {}
  @State lastUserId: number = -1

  onUserInfoChanged() {
    const uid = (this.userInfo?.id as number) || 3
    if (uid === this.lastUserId) {
      return
    }
    this.lastUserId = uid
    this.recommendSongListData()
  }
  // 获取轮播图接口
  async getSwiperData(){  /// async...await
    const res = await getSwiperList()
    this.swiperList = JSON.parse(JSON.stringify(res.data))
    console.log('swiperList'+ JSON.stringify(this.swiperList))
  }
  // 获取推荐歌单接口 -> 改为获取AI推荐单曲
  async recommendSongListData(){
    try {
      // 优先使用AI推荐
      // Login.ets 存的是 data.user 本身（{id, username, ...}），不是 {user: {...}}。
      // 之前写成 userInfo.user.id，多套了一层，登录用户永远落到默认值 3（该用户不存在
      // 于 consumers，于是所有人看到的都是热门兜底）。
      const userId = (this.userInfo?.id as number) || 3; // 未登录时用 3 -> 后端走热门兜底
      this.lastUserId = userId
      const res = await getRecommendSongs(userId, 6);
      // 用户信息异步到位：如果等待期间 userId 变了（3 -> 真实 id），这个响应已过期，
      // 直接丢弃，否则较慢的旧响应会覆盖掉新结果。
      if (userId !== this.lastUserId) {
        return
      }
      // 检查响应是否包含code属性（后端返回格式）或success属性（ResponseModel格式）
      const resRecord = res as Record<string, ESObject>;
      const isSuccess = resRecord.code === 200 || res.success;
      const responseData: ESObject = resRecord.data || res.data;
      
      if (isSuccess && responseData) {
        this.recommendSongs = responseData as RecommendSongModel[];
        console.log('AI推荐单曲:', JSON.stringify(this.recommendSongs));
      } else {
        // 降级到原有歌单推荐
        const fallbackRes = await getRecommendSongList();
        this.songList = JSON.parse(JSON.stringify(fallbackRes.data));
      }
    } catch (error) {
      console.error('推荐获取失败:', error);
      // 降级到原有歌单推荐
      const fallbackRes = await getRecommendSongList();
      this.songList = JSON.parse(JSON.stringify(fallbackRes.data));
    }
  }
  // 获取推荐歌手接口
  async recommendSingerData(){
    const res = await getRecommendSinger()
    this.singerList = JSON.parse(JSON.stringify(res.data))

  }
  // 生命周期
  aboutToAppear(): void {
    this.getSwiperData()
    this.recommendSongListData()
    this.recommendSingerData()
  }
  @Builder
  getSwiperListWidget(){
    Swiper() {
      ForEach(this.swiperList, (item: SwiperModel) => {
        Image(Config.resolveUrl(item.imgurl))
          .width('100%')
          .height('400lpx')
          .objectFit(ImageFit.Cover)
      }, (item: SwiperModel) => item.id.toString())
    }
    .loop(true)  //无限循环
    .autoPlay(true) // 自动播放
    .interval(2000) // 轮播间隔时间
    //.displayArrow(true)
    .indicator(  // 指示器
      Indicator.dot()
        .left(0)
        .itemWidth('30lpx')
        .itemHeight('30lpx')
        .selectedItemWidth('60lpx')
        .selectedItemHeight('30lpx')
        .color('#4A3F63')
        .selectedColor('#A855F7')
    )
    .height('460lpx')
    .width('92%')
    .borderRadius('24lpx')
    .clip(true)
    .margin({ top: '8lpx', bottom: '8lpx' })
  }
  @Builder
  getSongListWidget(){
    //   AI推荐单曲
    Column() {
      this.TitleBar('AI推荐单曲')
      // 推荐单曲列表
      if (this.recommendSongs.length > 0) {
        // 使用AI推荐结果
        Column({ space: '20lpx' }) {
          ForEach(this.recommendSongs, (item: RecommendSongModel) => {
            Row() {
              // 歌曲封面
              Image(Config.resolveUrl(item.pic))
                .width('120lpx')
                .height('120lpx')
                .borderRadius('12lpx')
                .objectFit(ImageFit.Cover)
              
              // 歌曲信息
              Column() {
                Row() {
                  Text(item.name)
                    .fontSize('32lpx')
                    .fontWeight(FontWeight.Medium)
                    .fontColor(Color.White)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })

                  // 推荐标签
                  Text('AI推荐')
                    .fontSize('20lpx')
                    .fontColor('#C9A6FF')
                    .backgroundColor('#2A2140')
                    .padding({ left: '8lpx', right: '8lpx', top: '4lpx', bottom: '4lpx' })
                    .borderRadius('8lpx')
                }
                .width('100%')
                .justifyContent(FlexAlign.SpaceBetween)
                .alignItems(VerticalAlign.Center)
                
                if (item.singer?.name) {
                  Text(item.singer.name)
                    .fontSize('24lpx')
                    .fontColor('#A8A3B8')
                    .margin({ top: '8lpx' })
                }

                if (item.reason) {
                  Text(item.reason)
                    .fontSize('20lpx')
                    .fontColor('#8B7BA8')
                    .margin({ top: '8lpx' })
                }
              }
              .layoutWeight(1)
              .alignItems(HorizontalAlign.Start)
              .margin({ left: '24lpx' })
              
              // 播放按钮
              Text("▶")
                .width('48lpx')
                .height('48lpx')
                .fontSize('24lpx')
                .fontColor('#A855F7')
                .textAlign(TextAlign.Center)
                .borderRadius('24lpx')
                .backgroundColor('#2A2140')
            }
            .width('100%')
            .padding('16lpx')
            .backgroundColor('#1C1826')
            .borderRadius('16lpx')
            .border({ width: 1, color: '#2B2538' })
            .onClick(() => {
              // 播放推荐歌曲
              this.playRecommendSong(item);
            })
          })
        }
        .padding({
          left: '16lpx',
          right: '16lpx'
        })
      } else {
        // 降级显示原有歌单
        Row() {
          Flex({
            wrap:FlexWrap.Wrap
          }){
            ForEach(this.songList, (item: SongListModel) => {
              Column() {
                Row() {
                  Image(Config.resolveUrl(item.pic))
                    .width('100%')
                    .aspectRatio(1)
                    .borderRadius('16lpx')
                  Text(item.style)
                    .fontColor(Color.White)
                    .fontSize('24lpx')
                    .fontWeight(FontWeight.Bold)
                    .position({
                      x: '12lpx',
                      y: '12lpx'
                    })
                }
                Text(item.title)
                  .textAlign(TextAlign.Start)
                  .fontSize('24lpx')
                  .fontColor(Color.White)
                  .maxLines(2)
                  .textOverflow({
                    overflow: TextOverflow.Ellipsis
                  })
                  .margin({
                    top: '16lpx'
                  })
              }
              .justifyContent(FlexAlign.SpaceBetween)
              .width('33%')
              .onClick(()=>{
                router.pushUrl({
                  url: 'pages/SongListDetail',
                  params: {
                    cid: item.id
                  }
                })
              })
            })
          }
        }
        .padding({
          left: '16lpx',
          right: '16lpx'
        })
      }
    }
  }
  
  // 播放推荐歌曲的方法
  playRecommendSong(song: RecommendSongModel) {
    // 原实现只 emit 了一条 UI 状态事件，播放器从未启动：
    // 界面上看着在播，实际没声音，也不会写进听歌记录。
    // 走 playSingle 才会真正加载 /stream/{id} 并触发记录。
    const songModel = new SongModel(song.id, song.name, song.pic || '', song.url || '')
    AvPlayerUtils.playSingle(songModel)
    console.log('开始播放AI推荐歌曲:', song.name)
  }
  @Builder
  getSingerWidget(){
    // 推荐歌手
    Column() {
      this.TitleBar('推荐歌手')
      // 歌单项
      Row() {
        Scroll() {
          Row({ space: '16lpx' }) {
            ForEach(this.singerList, (item: SingerModel) => {
              Column() {
                Row() {
                  Image(Config.resolveUrl(item.pic))
                    .width('100%')
                    .borderRadius('16lpx')
                  Text(item.name)
                    .fontColor(Color.White)
                    .fontSize('24lpx')
                    .fontWeight(FontWeight.Bold)
                    .position({
                      x: '12lpx',
                      y: '12lpx'
                    })
                }
                Text(item.name)
                  .textAlign(TextAlign.Start)
                  .fontSize('24lpx')
                  .fontColor(Color.White)
                  .maxLines(2)
                  .textOverflow({
                    overflow: TextOverflow.Ellipsis
                  })
                  .margin({
                    top: '16lpx'
                  })
              }
              .justifyContent(FlexAlign.SpaceBetween)
              .width('30%')
              .onClick(()=>{
                router.pushUrl({
                  url: 'pages/SingerDetail',
                  params: {
                    cid: item.id
                  }
                })
              })
            })
          }
        }
        .width('100%')
        .scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off)
      }
      .padding({
        left: '16lpx',
        right: '16lpx'
      })
    }
  }
  // 标题
  @Builder
  TitleBar(title: string) {
    // 标题
    Row() {
      Text(title)
        .fontWeight(FontWeight.Bold)
        .fontColor(Color.White)
        .fontSize('36lpx')
      Text('更多')
        .fontColor('#8B7BA8')
        .onClick(() => {
          if (title=='推荐歌单') {
            router.pushUrl({url:'pages/AllSongList'})
          }else{
            router.pushUrl({url:'pages/AllSingerList'})
          }
        })
    }
    .width('100%')
    .height('100lpx')
    .padding('16lpx')
    .justifyContent(FlexAlign.SpaceBetween)
  }
  // 搜索框
  @Builder
  SearchInput() {
    Row({ space: '16lpx' }) {
      Row({ space: '8lpx' }) {
        Image($r('app.media.ic_search'))
          .width('40lpx')
          .aspectRatio(1)
          .fillColor('#8B7BA8')
        Text('生如夏花🔥')
          .fontColor('#8B7BA8')
          .fontSize('28lpx')
          .layoutWeight(1)
      }
      .layoutWeight(1)
      .padding('24lpx')
      .borderRadius('60')
      .backgroundColor('#1C1826')
      .onClick(()=> {
        router.pushUrl({
          url: "pages/Search1"
        })
      })

      Text('AI 搜歌')
        .fontSize('24lpx')
        .fontWeight(FontWeight.Medium)
        .fontColor(Color.White)
        .padding({ left: '24lpx', right: '24lpx', top: '22lpx', bottom: '22lpx' })
        .borderRadius('48lpx')
        .backgroundColor('#A855F7')
        .shadow({ radius: 18, color: '#55A855F7', offsetX: 0, offsetY: 6 })
        .onClick(() => {
          // userInfo may still be {} right after a cold start; a persisted token
          // is enough to enter. Expired tokens are handled inside AiSearch.
          if (this.userInfo && this.userInfo['id']) {
            router.pushUrl({ url: 'pages/AiSearch' })
            return
          }
          this.openAiSearch()
        })
    }
    .width('100%')
    .alignItems(VerticalAlign.Center)
    .padding({ left: '24lpx', right: '24lpx', top: '16lpx', bottom: '12lpx' })
  }
  private async openAiSearch() {
    const token = await preferencesUtil.get('userinfoStore', 'token', '') as string
    router.pushUrl({ url: token ? 'pages/AiSearch' : 'pages/Login' })
  }

  build() {
    Column() {
      Scroll(){
        Column() {
          this.SearchInput()
          this.getSwiperListWidget()
          this.getSongListWidget()
          this.getSingerWidget()
        }
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#0E0C14')
  }
}