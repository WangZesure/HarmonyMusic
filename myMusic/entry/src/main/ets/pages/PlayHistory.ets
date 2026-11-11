import router from '@ohos.router'
import promptAction from '@ohos.promptAction'
import { getPlayHistory } from './common/Api'
import { ConfigUtil as Config } from './common/ConfigUtil'
import { SongModel } from './model/AppModel'
import { AvPlayerUtils } from './utils/AvPlayerUtils'

// 后端 /getPlayHistory 返回：歌曲字段 + singer 关联 + playTime / playDuration
interface PlayHistoryItem {
  id: number
  name: string
  pic: string
  url: string
  playTime: string
  playDuration: number
  singer?: SingerBrief
}

interface SingerBrief {
  name: string
}

@Entry
@Component
struct PlayHistory {
  @State list: PlayHistoryItem[] = []
  @State loading: boolean = true
  @State errMsg: string = ''

  aboutToAppear(): void {
    this.load()
  }

  async load() {
    this.loading = true
    const res = await getPlayHistory(100)
    if (res.success) {
      this.list = (res.data || []) as PlayHistoryItem[]
      this.errMsg = ''
    } else {
      this.errMsg = res.message || '加载失败'
    }
    this.loading = false
  }

  // 2026-11-11T09:30:52.000Z -> 11-11 17:30（本地时区）
  formatTime(iso: string): string {
    if (!iso) { return '' }
    const d = new Date(iso)
    if (isNaN(d.getTime())) { return '' }
    const mm = (d.getMonth() + 1).toString().padStart(2, '0')
    const dd = d.getDate().toString().padStart(2, '0')
    const hh = d.getHours().toString().padStart(2, '0')
    const mi = d.getMinutes().toString().padStart(2, '0')
    return `${mm}-${dd} ${hh}:${mi}`
  }

  play(item: PlayHistoryItem) {
    const song = new SongModel(item.id, item.name, item.pic || '', item.url || '')
    AvPlayerUtils.playSingle(song)
    promptAction.showToast({ message: `正在播放：${item.name}`, duration: 2000 })
  }

  build() {
    Column() {
      // 顶栏
      Row() {
        Text('‹')
          .fontSize('44lpx')
          .fontColor(Color.White)
          .onClick(() => { router.back() })
        Text('听歌记录')
          .fontSize('34lpx')
          .fontColor(Color.White)
          .fontWeight(FontWeight.Bold)
          .margin({ left: '10lpx' })
        Blank()
        Text(`${this.list.length} 首`)
          .fontSize('24lpx')
          .fontColor('#6E6880')
          .margin({ right: '8lpx' })
      }
      .width('100%')
      .height('90lpx')
      .padding({ left: '20lpx', right: '20lpx' })
      .backgroundColor('#17141F')
      .border({ width: { bottom: 1 }, color: '#2B2538' })

      if (this.loading) {
        Column() {
          Text('加载中…').fontSize('26lpx').fontColor('#6E6880')
        }
        .layoutWeight(1)
        .width('100%')
        .justifyContent(FlexAlign.Center)
      } else if (this.errMsg) {
        Column({ space: '24lpx' }) {
          Text(this.errMsg).fontSize('26lpx').fontColor('#A8A3B8')
          Button('重试')
            .backgroundColor('#2A2140')
            .fontColor('#C9A6FF')
            .onClick(() => { this.load() })
        }
        .layoutWeight(1)
        .width('100%')
        .justifyContent(FlexAlign.Center)
      } else if (this.list.length === 0) {
        Column({ space: '16lpx' }) {
          Text('还没有听歌记录').fontSize('30lpx').fontColor('#A8A3B8')
          Text('去首页随便放一首吧').fontSize('24lpx').fontColor('#6E6880')
        }
        .layoutWeight(1)
        .width('100%')
        .justifyContent(FlexAlign.Center)
      } else {
        List({ space: '12lpx' }) {
          ForEach(this.list, (item: PlayHistoryItem) => {
            ListItem() {
              Row() {
                Image(Config.resolveUrl(item.pic))
                  .width('100lpx')
                  .height('100lpx')
                  .borderRadius('12lpx')
                  .objectFit(ImageFit.Cover)
                Column({ space: '6lpx' }) {
                  Text(item.name)
                    .fontSize('30lpx')
                    .fontWeight(FontWeight.Medium)
                    .fontColor(Color.White)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                  Text(item.singer?.name || '未知歌手')
                    .fontSize('24lpx')
                    .fontColor('#A8A3B8')
                    .maxLines(1)
                  Text(`听过 · ${this.formatTime(item.playTime)}`)
                    .fontSize('20lpx')
                    .fontColor('#6E6880')
                }
                .layoutWeight(1)
                .alignItems(HorizontalAlign.Start)
                .margin({ left: '20lpx' })

                Image($r('app.media.ic_play'))
                  .width('40lpx')
                  .aspectRatio(1)
                  .fillColor('#A855F7')
              }
              .width('100%')
              .padding('16lpx')
              .backgroundColor('#1C1826')
              .borderRadius('16lpx')
              .alignItems(VerticalAlign.Center)
              .onClick(() => { this.play(item) })
            }
          }, (item: PlayHistoryItem) => item.id.toString())
        }
        .layoutWeight(1)
        .width('100%')
        .padding({ left: '20lpx', right: '20lpx', top: '16lpx' })
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#0E0C14')
  }
}
