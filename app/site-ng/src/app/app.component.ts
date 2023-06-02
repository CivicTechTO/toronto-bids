import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { JsonPipe } from '@angular/common';
import { CommodityType, SearchQuery } from './models/models';
import { MatNativeDateModule } from '@angular/material/core';
import { MatDatepicker } from '@angular/material/datepicker';
import { MatLegacyCard as MatCard } from '@angular/material/legacy-card';
import { Observable } from 'rxjs';
@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.less'],
})
/*
  TODO
  - Connect to API
  - Create a results detail view that opens in separate page
  - Clean up styling classes
  - Optimize for mobile responsiveness
    - Make the side drawer toggle-able, and turn into menu that opens up from the bottom on mobile
    -
*/

export class AppComponent {
  title = 'TorontoBidsFrontend';
  divisions = ['Division A', 'Division B', 'Division C'];
  commodities = [{ value: CommodityType.Any, display: 'Any' }, { value: CommodityType.ConstructionServices, display: 'Construction Services' }, { value: CommodityType.GoodsAndServices, display: 'Goods and Services' }, { value: CommodityType.ProfessionalServices, display: 'Professional Services' }];
  types = ['Type A', 'Type B', 'Type C'];
  buyer = '';

  constructor() {

  }
}
