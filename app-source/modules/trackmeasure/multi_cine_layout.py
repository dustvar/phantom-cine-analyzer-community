"""Presentation-only ordering and independent view controls for Cine panes."""
from PySide6.QtCore import QObject,QEvent,Qt,QPoint,QPointF,QTimer,QSignalBlocker
from PySide6.QtWidgets import QComboBox,QFrame,QHBoxLayout,QToolButton,QMenu,QLabel
from PySide6.QtGui import QTransform
from view_inspection import InspectionBar

LAYOUTS=(('Auto','auto'),('Grid','grid'),('Side by side','side_by_side'),('Stacked','stacked'))


def pane_positions(count,mode):
    if mode=='side_by_side':return [(0,i,1,1) for i in range(count)]
    if mode=='stacked':return [(i,0,1,1) for i in range(count)]
    if mode=='grid':return [(i//2,i%2,1,1) for i in range(count)] if count>1 else [(0,0,1,1)]
    return {1:[(0,0,1,2)],2:[(0,0,1,1),(0,1,1,1)],
        3:[(0,0,1,1),(0,1,1,1),(1,0,1,2)],
        4:[(0,0,1,1),(0,1,1,1),(1,0,1,1),(1,1,1,1)]}[count]


class PaneViewControls(QObject):
    def __init__(self,pane,window):
        super().__init__(pane);self.pane=pane;self.window=window
        self.move=QToolButton(pane);self.move.setText('↔');self.move.setFixedSize(26,24)
        self.move.setStyleSheet('QToolButton { padding: 1px; } QToolButton::menu-indicator { image: none; width: 0px; }')
        self.move.setAccessibleName(f'Move Cine C{pane.pane_index+1}')
        self.move.setToolTip('Move this Cine to another screen position')
        self.move.setPopupMode(QToolButton.InstantPopup)
        self.menu=QMenu(self.move);self.move.setMenu(self.menu);self.menu.aboutToShow.connect(self.fill_menu)
        pane.header.insertWidget(pane.header.count()-1,self.move)
        self.bar = InspectionBar(pane.graph, f'Cine C{pane.pane_index + 1}')
        self.minus, self.fit, self.plus = self.bar.minus, self.bar.fit, self.bar.plus
        pane.graph.viewport().installEventFilter(self)
        pane.title.installEventFilter(self);pane.title.setCursor(Qt.PointingHandCursor)
        pane.title.setToolTip('Click to make this Cine active. Zoom and drag each video independently.')
        self.position()

    def eventFilter(self,obj,event):
        if obj is self.pane.title and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
            self.window.activate_cine_pane(self.pane.pane_index);return True
        if obj is self.pane.graph.viewport() and event.type() in (QEvent.Resize,QEvent.Show):self.position()
        return False

    def position(self):
        self.bar.move(6,6);self.bar.setVisible(not bool(getattr(self.window,'_guide',None)))
        self.bar.raise_()

    def fill_menu(self):
        self.menu.clear();manager=self.window.pane_layout_manager
        visible=manager.visible_order();current=visible.index(self.pane.pane_index) if self.pane.pane_index in visible else -1
        for position,index in enumerate(visible):
            action=self.menu.addAction(f'Move to position {position+1}'+(' · current' if position==current else ''))
            action.setEnabled(position!=current)
            action.triggered.connect(lambda checked=False,p=position:manager.move_pane(self.pane.pane_index,p))
        self.menu.addSeparator()
        hide=self.menu.addAction('Hide this Cine')
        hide.setEnabled(len(visible)>1)
        hide.triggered.connect(lambda:self.window.hide_cine_pane(self.pane.pane_index))


class MultiCineLayout(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.generation=0
        window.pane_order=list(range(4));window.pane_layout='auto'
        self.choice=QComboBox(window.workspace_header);self.choice.setAccessibleName('Cine pane layout')
        self.choice.setToolTip('Arrange visible Cines. Use ↔ in each Cine header to change its position.')
        for label,value in LAYOUTS:self.choice.addItem(label,value)
        self.choice.installEventFilter(self)
        # The source row is nested inside the global header.
        def locate(layout):
            for i in range(layout.count()):
                item=layout.itemAt(i)
                if item.widget() is window.workspace_count_label:return layout,i
                if item.layout():
                    found=locate(item.layout())
                    if found:return found
        target,index=locate(window.workspace_header.layout())
        self.layout_label = QLabel('Layout')
        target.insertWidget(index,self.layout_label);target.insertWidget(index+1,self.choice)
        self.choice.currentIndexChanged.connect(self.set_layout)
        for pane in window.workspace_panes:pane.view_controls=PaneViewControls(pane,window)
        self.update_metrics()

    def update_metrics(self):self.choice.setFixedWidth(max(104,self.choice.sizeHint().width()))

    def eventFilter(self,obj,event):
        if obj is self.choice and event.type() in (QEvent.FontChange,QEvent.StyleChange):
            QTimer.singleShot(0,self.window,self.update_metrics)
        return False

    def count(self):return len(getattr(getattr(self.window,'vm',None),'workspace_contexts',()))

    def visible_order(self,count=None):
        w=self.window;count=self.count() if count is None else count
        order=[i for i in w.pane_order if i in range(count)]
        order.extend(i for i in range(count) if i not in order)
        return [i for i in order if i not in w.hidden_cine_indices]

    def set_layout(self,index):
        self.window.pane_layout=self.choice.itemData(index);self.configure(max(1,self.count()))

    def move_pane(self,index,destination):
        if getattr(self.window,'_guide',None):return
        visible=self.visible_order()
        if index not in visible or not 0<=destination<len(visible):return
        visible.remove(index);visible.insert(destination,index)
        # Hidden identities keep their slots and reappear predictably.
        iterator=iter(visible);w=self.window
        w.pane_order=[next(iterator) if i in visible else i for i in w.pane_order]
        self.configure(self.count())

    def configure(self,count):
        w=self.window;count=max(1,min(4,int(count)));self.generation+=1;generation=self.generation
        order=[i for i in w.pane_order if i in range(count)];order.extend(i for i in range(count) if i not in order)
        w.pane_order=order+[i for i in range(count,4)]
        visible=self.visible_order(count)
        if not visible:w.hidden_cine_indices.discard(order[0]);visible=[order[0]]
        mode=w.pane_layout if w.pane_layout in dict((value,label) for label,value in LAYOUTS) else 'auto'
        with QSignalBlocker(self.choice):self.choice.setCurrentIndex(self.choice.findData(mode))
        self.choice.setEnabled(count>1 and not bool(getattr(w,'_guide',None)))
        views={pane.pane_index:(QTransform(pane.graph.transform()),QPointF(pane.graph.mapToScene(pane.graph.viewport().rect().center())),
            pane.graph._view_zoom,getattr(pane.graph,'_pca_rotation',0),pane.graph.scene().sceneRect()) for pane in w.workspace_panes if pane.graph._view_zoom>1}
        grid=w.workspace_grid_layout
        for pane in w.workspace_panes:grid.removeWidget(pane)
        for row in range(grid.rowCount()):grid.setRowStretch(row,0);grid.setRowMinimumHeight(row,0)
        for column in range(grid.columnCount()):grid.setColumnStretch(column,0);grid.setColumnMinimumWidth(column,0)
        positions=dict(zip(visible,pane_positions(len(visible),mode)))
        for i,pane in enumerate(w.workspace_panes):
            pane.setVisible(i in visible);pane.hide_button.setEnabled(len(visible)>1)
            w.workspace_restore_buttons[i].setVisible(i<count and i in w.hidden_cine_indices)
            pane.view_controls.move.setVisible(count>1)
            if i in positions:
                row,column,rows,columns=positions[i];grid.addWidget(pane,row,column,rows,columns)
                for r in range(row,row+rows):grid.setRowStretch(r,1)
                for c in range(column,column+columns):grid.setColumnStretch(c,1)
        w.hidden_cines_bar.setVisible(bool(w.hidden_cine_indices))
        def settle():
            if generation!=self.generation:return
            for index,(transform,center,zoom,rotation,rect) in views.items():
                graph=w.workspace_panes[index].graph
                if (getattr(graph,'_pca_rotation',0)==rotation and graph.scene().sceneRect()==rect
                        and graph._view_zoom==zoom):
                    graph.setTransform(transform);graph._view_zoom=zoom;graph.centerOn(center)
            w._position_tracking_overlay_controls();self.sync_controls()
            w._position_playback()
            if hasattr(w,'_sync_group_workspace'):w._sync_group_workspace()
        QTimer.singleShot(0,w,settle)

    def sync_controls(self):
        guided=bool(getattr(self.window,'_guide',None))
        self.choice.setEnabled(self.count()>1 and not guided)
        for pane in self.window.workspace_panes:
            pane.view_controls.move.setEnabled(not guided)
            pane.hide_button.setEnabled(not guided and len(self.visible_order())>1)
            pane.view_controls.position()
