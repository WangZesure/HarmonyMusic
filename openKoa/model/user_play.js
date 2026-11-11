// 用户播放记录（表已由 db/schema_v1.sql 建好，这里只做映射）
// 注意：表里的时间列是 created_at / updated_at（下划线），
// Sequelize 默认用 createdAt / updatedAt，必须显式映射，否则 INSERT 会报未知列。
module.exports = (sequelize, DataTypes) => {
    const user_play = sequelize.define("user_play", {
      id: {
        type: DataTypes.INTEGER,
        primaryKey: true,
        autoIncrement: true,
      },
      user_id: {
        type: DataTypes.INTEGER,
      },
      song_id: {
        type: DataTypes.INTEGER,
      },
      play_duration: {
        type: DataTypes.INTEGER,
      },
      full_duration: {
        type: DataTypes.INTEGER,
      },
      is_liked: {
        type: DataTypes.TINYINT,
      },
      play_time: {
        type: DataTypes.DATE,
      },
      device_type: {
        type: DataTypes.STRING(20),
      },
      source: {
        type: DataTypes.ENUM('real', 'synthetic'),
      },
    }, {
      tableName: 'user_plays',
      createdAt: 'created_at',
      updatedAt: 'updated_at',
    });
    return user_play;
};
