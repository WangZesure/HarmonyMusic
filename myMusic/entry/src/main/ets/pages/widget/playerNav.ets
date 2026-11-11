import { PlayStateType } from '../model/PlayStateModel'
import {ConfigUtil as Config} from '../common/ConfigUtil'
@Component
export default struct PlayerNav {
  @StorageLink("isLogin") isLogin: boolean = false
  @Link playState: PlayStateType
  build() {
    Row() {
      Row({space:'20lpx'}) {
        Image(Config.resolveUrl(this.playState.pic))
          .width('80lpx')
          .aspectRatio(1)
          .borderRadius('12lpx')
          .objectFit(ImageFit.Cover)
        Column() {
          Text(this.playState.name)
            .fontColor('#FFFFFF')
            .fontSize('28lpx')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }.alignItems(HorizontalAlign.Start)
      }.height('100%')
      .alignItems(VerticalAlign.Center)
      Row({space:'32lpx'}) {
        Image($r("app.media.ic_prev"))
          .width('40lpx')
          .aspectRatio(1)
          .fillColor('#C9A6FF')
        Image( this.playState.isPlay?$r('app.media.ic_paused'): $r('app.media.ic_play'))
          .fillColor('#A855F7')
          .width('48lpx')
          .aspectRatio(1)
        Image($r('app.media.ic_next'))
          .width('40lpx')
          .aspectRatio(1)
          .fillColor('#C9A6FF')
      }.height('100%')
    }
    .width('100%')
    .height('120lpx')
    .backgroundColor('#17141F')
    .border({ width: { top: 1 }, color: '#2B2538' })
    .justifyContent(FlexAlign.SpaceBetween)
    .padding({ left: '40lpx', right: '40lpx' })
  }
}
