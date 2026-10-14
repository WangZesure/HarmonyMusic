export  class ConfigUtil{
  static readonly SERVER="http://localhost:3000"//10.136.216.251
  static readonly SERVER_URL="http://localhost:3000/uploads/"
  static readonly HTTP_CODE_200=200  // 成功
  /**
   * 成功失败提示
   */
  static readonly SERVER_NET_SUCCESS: string = '请求数据成功';
  static readonly SERVER_NET_ERROR: string = '请求失败请检查网络';

  static readonly HTTP_CONNECT_TIMEOUT: number = 10000;
  static readonly HTTP_READ_TIMEOUT: number = 10000;

  /**
   * 统一解析媒体地址（契约 C5）：
   *  - 以 http(s):// 开头：直接使用（Jamendo 封面等外链）
   *  - 以 /stream/ 等服务器根路径开头：拼 Config.SERVER（Koa 的播放接口）
   *  - 其余相对路径（老数据的 /img、/song）：拼 Config.SERVER_URL，兼容旧数据
   */
  static resolveUrl(u: string | undefined): string {
    if (!u) return '';
    if (u.startsWith('http://') || u.startsWith('https://')) return u;
    if (u.startsWith('/img/') || u.startsWith('/song/') || u.startsWith('/uploads/')) {
      return ConfigUtil.SERVER_URL + u;
    }
    if (u.startsWith('/')) return ConfigUtil.SERVER + u;
    return ConfigUtil.SERVER_URL + u;
  }
}