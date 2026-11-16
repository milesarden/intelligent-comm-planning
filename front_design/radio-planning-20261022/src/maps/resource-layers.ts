import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import VectorSource from 'ol/source/Vector';
import VectorLayer from 'ol/layer/Vector';
import { Circle,Fill,Stroke,Style,Text } from 'ol/style';
import type { Coordinate,InterferenceSource } from '../types/domain';
import { projectCoordinate } from './layers';
import { GIS_SITUATION_LABELS, gisSymbol } from './gis-symbols';

// 仅显示资源点位和取点预览，绝不推算干扰影响范围。
export function interferenceLayer(rows:InterferenceSource[],selectedId='',readonlySituation=false){
  return new VectorLayer({source:new VectorSource({features:rows.map(row=>new Feature({geometry:new Point(projectCoordinate(row.coordinate)),kind:'interference-source',objectId:row.id,source:row}))}),
    declutter:readonlySituation ? GIS_SITUATION_LABELS.group : undefined,
    style:(feature, resolution)=>{
      const row=feature.get('source') as InterferenceSource, selected=row.id===selectedId;
      const image=gisSymbol('interference-source',selected,row.active?'#e4a859':'#6b7c91');
      const showName=selected || readonlySituation && resolution <= GIS_SITUATION_LABELS.detailResolution;
      const text=showName ? new Text({text:row.name+' · 干扰源',offsetY:-20,
        font:readonlySituation ? GIS_SITUATION_LABELS.font : undefined,
        declutterMode:readonlySituation ? 'declutter' : undefined,
        fill:new Fill({color:'#ffda9d'}),backgroundFill:new Fill({color:'#162238'}),padding:[4,4,4,4]}) : undefined;
      const symbol=new Style({image,zIndex:20});
      return readonlySituation && text ? [symbol,new Style({text,zIndex:20})]
        : text ? new Style({image,text,zIndex:20}) : symbol;
    }});
}
export function resourcePickLayer(point?:Coordinate){
  return new VectorLayer({source:new VectorSource({features:point?[new Feature({geometry:new Point(projectCoordinate(point)),kind:'resource-pick'})]:[]}),
    style:new Style({image:new Circle({radius:10,stroke:new Stroke({color:'#fff',width:2,lineDash:[3,3]}),fill:new Fill({color:'#ffffff22'})}),text:new Text({text:'未应用位置',offsetY:20,fill:new Fill({color:'#ffffff'})}),zIndex:30})});
}
