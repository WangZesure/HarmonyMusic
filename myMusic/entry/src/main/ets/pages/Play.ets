import emitter from '@ohos.events.emitter'
import { EmitEventType } from './model/paramModel'
import { SongModel } from './model/AppModel'
import { defaultState, PlayStateModel, PlayStateType } from './model/PlayStateModel'
import { AvPlayerUtils } from './utils/AvPlayerUtils'
import playService  from './utils/PlayService'
import {ConfigUtil as Config} from './common/ConfigUtil'
@Entry
@Component
struct Play {
  @StorageLink("isLogin") isLogin: boolean = false
  @State opacityNum: number = 1
  @State panelHeight: string = '0%'
  //时长数字(ms)转字符串
  number2time(number: number) {
    const ms: number = number % 1000
    const second = (number - ms) / 1000
    const s: number = second % 60
    if (second > 60) {
      const m: number = (second - s) / 60 % 60
      return m.toString()
        .padStart(2, '0') + ':' + s.toString()
        .padStart(2, '0')
    }
    return '00:' + s.toString()
      .padStart(2, '0')
  }
  @Builder
  deleteButton(index:number) {
    Button('删除')
      .backgroundColor('#ec5c87')
      .fontColor('#fff')
      .width(80)
      .height('100%')
      .type(ButtonType.Normal)
      .onClick(()=>{
        AvPlayerUtils.remove(index)
      })
  }
  //用于文字
  @State randomShakenX: number = 0
  @State randomShakenY: number = 0
  //唱片的变色效果
  @State randomNum: number = 0
  @State randomColor: string = 'rgb(0,0,0)'
  // 订阅事件
  @State
  @Watch("anniMethod") playState: PlayStateType = defaultState
  anniMethod(){
    //改变动画的颜色
    if(this.playState.isPlay){
      this.randomNum = Math.random() * 50
      this.randomShakenX = 2 - Math.random() * 4
      this.randomShakenY = 2 - Math.random() * 4
      this.randomColor = `rgb(${Math.floor(Math.random() * 255)},${Math.floor(Math.random() * 255)},${Math.floor(Math.random() * 255)})`
    }
  }
  async aboutToAppear() {
    emitter.on({ eventId: EmitEventType.UPDATE_STATE }, (data) => {
      this.playState = new PlayStateModel(JSON.parse(data.data!.playStateStr))
    })
    //从pre中获取状态数据
    this.playState = await playService.getState()
    console.log('logData', '进度条：', JSON.stringify(this.playState))
  }
  build() {
    Stack({ alignContent: Alignment.Bottom }) {
      // 播放
      Stack() {
        //内容
        Column() {
          //播放界面
          Column() {
            // 图片
            Stack({ alignContent: Alignment.Top }) {
              Row() {
                Row() {
                  Image(Config.resolveUrl(this.playState.pic))
                    .width('70%')
                    .borderRadius('800lpx')
                }
                .backgroundImage($r('app.media.ic_cd'))
                .backgroundImageSize(ImageSize.Cover)
                .justifyContent(FlexAlign.Center)
                .width('100%')
                .borderRadius('800lpx')
                .clip(true)
                .aspectRatio(1)
                .shadow({
                  radius: this.randomNum,
                  color: this.randomColor
                })
                .animation({
                  duration: 1000,
                })
              }
              .margin({
                top: '40lpx'
              })
              .width('74%')
              .aspectRatio(1)
              .justifyContent(FlexAlign.Center)
              .padding('24lpx')
              // 唱针
              Image($r('app.media.ic_stylus'))
                .width('400lpx')
                .aspectRatio(1)
                .rotate({
                  angle: this.playState.isPlay?-45:-55,
                  centerX: '200lpx',
                  centerY: '60lpx'
                })
                .animation({
                  duration: 500
                })
            }
            // 歌曲信息
            Column({ space: '10lpx' }) {
              Text(this.playState.isPlay ? '正在播放' : '已暂停')
                .fontSize('22lpx')
                .fontColor('#8B7BA8')
                .letterSpacing(4)
              Text(this.playState.name)
                .fontSize('52lpx')
                .fontWeight(FontWeight.Bold)
                .fontColor(Color.White)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .textAlign(TextAlign.Center)
            }
            .layoutWeight(1)
            .width('100%')
            .constraintSize({ minHeight: '150lpx' })
            .justifyContent(FlexAlign.Center)
            .padding({ left: '48lpx', right: '48lpx' })
            // 操作
            Row({ space: '88lpx' }) {
              Image($r("app.media.ic_like"))
                .fillColor('#B9AECB')
                .width('44lpx')
                .aspectRatio(1)
              Image($r("app.media.ic_comment_o"))
                .fillColor('#B9AECB')
                .width('44lpx')
                .aspectRatio(1)
              Image($r("app.media.ic_bells_o"))
                .fillColor('#B9AECB')
                .width('44lpx')
                .aspectRatio(1)
              Image($r("app.media.ic_download_o"))
                .fillColor('#B9AECB')
                .width('44lpx')
                .aspectRatio(1)
            }
            .width('100%')
            .justifyContent(FlexAlign.Center)
            .margin({ bottom: '8lpx' })
            // 播放
            Column() {
              // 进度条
              Row({ space:'8lpx' }) {
                Text(this.number2time(this.playState.time))
                  .fontSize('24lpx')
                  .fontColor('#8B7BA8')
                Slider({
                  value: this.playState.time,
                  min: 0,
                  max: this.playState.duration
                })
                  .layoutWeight(1)
                  .blockColor('#A855F7')
                  .selectedColor('#A855F7')
                  .trackColor('#332B45')
                  .trackThickness(4)
                  .onChange((time)=>{
                    AvPlayerUtils.player.seek(time)  // AvPlayerUtils.player.seek跳转到指定时间
                  })

                Text(this.number2time(this.playState.duration))
                  .fontSize('24lpx')
                  .fontColor('#8B7BA8')
              }
              .width('100%')
              .padding('48lpx')
              // 切换
              Row() {
                if(this.playState.playMode==='auto'){
                  Image($r("app.media.ic_auto"))
                    .fillColor('#D8CFE8')
                    .width('60lpx')
                    .onClick(()=>{
                      AvPlayerUtils.changeMode('repeat')
                    })
                }else if(this.playState.playMode==='repeat'){
                  Image($r("app.media.ic_repeat"))
                    .fillColor('#D8CFE8')
                    .width('60lpx')
                    .onClick(()=>{
                      AvPlayerUtils.changeMode('random')
                    })
                }else if(this.playState.playMode==='random'){
                  Image($r("app.media.ic_random"))
                    .fillColor('#D8CFE8')
                    .width('60lpx')
                    .onClick(()=>{
                      AvPlayerUtils.changeMode('auto')
                    })
                }

                Image($r('app.media.ic_prev'))
                  .fillColor('#D8CFE8')
                  .width('60lpx')
                  .onClick(()=>{
                    AvPlayerUtils.prev()
                  })

                // 播放按钮
                Row() {
                  Image(this.playState.isPlay?$r('app.media.ic_paused'):$r('app.media.ic_play'))
                    .fillColor(Color.White)
                    .width('72lpx')
                    .aspectRatio(1)
                }
                .width('132lpx')
                .aspectRatio(1)
                .borderRadius('66lpx')
                .backgroundColor('#A855F7')
                .justifyContent(FlexAlign.Center)
                .shadow({ radius: 28, color: '#66A855F7', offsetX: 0, offsetY: 10 })
                .onClick(()=>{
                    this.playState.isPlay? AvPlayerUtils.pause(): AvPlayerUtils.playSingle(this.playState.playList[this.playState.playIndex])
                })
                // 下一首
                Image($r('app.media.ic_next'))
                  .fillColor('#D8CFE8')
                  .width('60lpx')
                  .onClick(()=>{
                    AvPlayerUtils.next()
                  })
                // 播放列表
                Image($r('app.media.ic_song_list'))
                  .fillColor('#D8CFE8')
                  .width('60lpx')
                  .onClick(() => {
                    this.panelHeight = '100%'
                  })
              }
              .width('100%')
              .padding({
                bottom: '48lpx'
              })
              .justifyContent(FlexAlign.SpaceAround)
            }
            .width('100%')
          }
          .layoutWeight(1)
          .width('100%')
          .linearGradient({
            angle: 180,
            colors: [['#221A33', 0.0], ['#171222', 0.45], ['#0C0A12', 1.0]]
          })
        }
      }
      .backgroundColor(Color.Transparent)
      // 列表
      Column() {
        Column() {

        }
        .width('100%')
        .layoutWeight(1)
        .onClick(() => {
          this.panelHeight = '0%'
        })
        Column() {
          Row() {
            Row() {
              Image($r("app.media.ic_play"))
                .width('40lpx')
                .fillColor('#ff5186')
            }
            .width('100lpx')
            .aspectRatio(1)
            .justifyContent(FlexAlign.Center)

            Row({ space: '16lpx' }) {
              Text(`播放列表 (${this.playState.playList.length})`)
                .fontColor(Color.White)
                .fontSize('28lpx')
            }
            .layoutWeight(1)

            Image($r('app.media.ic_close'))
              .fillColor('#ffa49a9a')
              .width('48lpx')
              .height('48lpx')
              .margin({ right: '32lpx' })
              .onClick(() => {
                this.panelHeight = '0%'
              })
          }
          .width('100%')
          .backgroundColor('#ff353333')
          .padding('16lpx')
          .border({
            width: { bottom: 1 },
            color: '#12ec5c87'
          })
          .borderRadius({
            topLeft: '8lpx',
            topRight: '8lpx'
          })
          // 播放列表
          List() {
            ForEach(this.playState.playList, (item: SongModel, index: number) => {
              ListItem() {
                Row() {
                  Row() {
                    Text((index + 1).toString())
                      .fontColor('#ffa49a9a')
                  }
                  .width('100lpx')
                  .aspectRatio(1)
                  .justifyContent(FlexAlign.Center)
                  // 列表
                  Row({ space: '20lpx' }) {
                    Column() {
                      Text(item.name)
                        .fontSize('28lpx')
                        .fontColor(this.playState.playIndex===index ?'#ff5186': '#ffffff')
                    }
                    .layoutWeight(1)
                    .alignItems(HorizontalAlign.Start)
                    .justifyContent(FlexAlign.Center)
                    .onClick(()=>{
                      AvPlayerUtils.playSingle(item)
                    })
                  }
                  .layoutWeight(1)

                  Image($r('app.media.ic_more'))
                    .width('48lpx')
                    .height('48lpx')
                    .margin({ right: '32lpx' })
                    .fillColor(Color.Gray)
                }
                .alignItems(VerticalAlign.Center)
              }
              .swipeAction({
                end: this.deleteButton(index)
              })
              .border({
                width: { bottom: 1 },
                color: '#12ec5c87'
              })
            })
          }
          .width('100%')
          .height('100%')
          .backgroundColor('#ff353333')
        }
        .height('800lpx')
      }
      .height(this.panelHeight)
      .animation({
        duration: 300
      })

    }
    .width('100%')
    .height('100%')
    .backgroundColor(Color.Transparent)
    .opacity(this.opacityNum)

  }
  // 页面也换效果,避免滑动切换效果
  pageTransition() {
    PageTransitionEnter({ duration: 200 })
      .onEnter(() => {
        this.opacityNum = 1
      })
    PageTransitionExit({ duration: 200 })
      .onExit(() => {
        this.opacityNum = 0
      })
  }
}